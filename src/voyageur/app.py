"""
===============================================================================
HTTP API (FastAPI: a thin shell over the heal session)
===============================================================================
The routes a client talks to, all under /v1 except the liveness check. The shell does only what HTTP needs: bearer
authentication, reading a body with a hard size limit, turning the core's refusals into status codes, and shaping filters.
Every decision is made by the HealSession passed to create_app (`create_app(core)`), so tests and the offline CLI use the same
core with a fake model.

    GET  /healthz                      liveness (no token)
    GET  /v1/capabilities              version, models per role (no endpoint, no key), vision, limits, languages
    GET  /v1/probe.js                  the in-page probe both clients run, served from package data
    POST /v1/heal                      start a heal: a candidate to try, or a decision
    POST /v1/heals/{id}/result         what the client saw when it tried the candidate: the next candidate or the decision
    GET  /v1/heals/{id}                the audit record (counts, model calls, candidates tried, outcome; no file contents)
    GET  /v1/heals/{id}/overlay.png    the marked screenshot a vision model was shown for that heal (404 when it made no vision call)
    GET  /v1/heals                     the filtered list behind any aggregate, each row linking to its record
    GET  /v1/metrics                   the aggregates (metrics.aggregate), same filters
    POST /v1/heals/{id}/followup       what the client saw after the PR: merged unchanged, edited, reverted, ...
    POST /v1/audit                     rank every locator in the sent files against the inventory (no model)
    POST /v1/diagnose                  name the likely cause of a failure that is not locator drift (off unless HEAL_DIAGNOSE=on)
    GET  /metrics                      Prometheus text exposition (bearer token like the /v1 routes)
    GET  /dashboard                    the built-in dashboard page (no token to load it; its data calls carry the token)
    GET  /docs, GET /openapi.json      the API page (Swagger UI, "Try it out") and the OpenAPI document (apidocs.py)

Features:
    ✓ Bearer token (HEAL_SERVICE_TOKEN) on everything under /v1, compared in constant time; no token configured means an
      open API, which the CLI only allows on a loopback bind
    ✓ 413 for a body over 10 MB (by Content-Length, and again while streaming), too many files, a file over 256 KB, too many
      inventory entries or an oversized aria snapshot; 422 for a request that carries a value, a cookie or a storage dump, or
      does not match the schema. Error bodies name the field and the rule, never the input
    ✓ A screenshot over 4 MB decoded is a 413; one that is not a PNG, has bad base64, a side over 8192 px, over 16 million pixels,
      dimensions that do not match the PNG, or comes with options.vision off is a 422 (screenshot.py). The overlay route is behind
      the same token as every other /v1 route and sends no-store
    ✓ 404 for an unknown heal, 409 for a result sent to a heal that already has its answer
    ✓ No route fetches a URL, opens a browser or runs a program: the page URL in a request is only ever recorded as a path
    ✓ GET /v1/heals takes a filter for every aggregate (outcome, reason, settled_at, tier_movement, run, repo, framework,
      pr_state, mode, model, role, ...), `sort=slowest` and `format=csv` (cells defused against spreadsheet formulas)
    ✓ /dashboard is one page with inline script and style only, served under a CSP that allows nothing else (no CDN, no
      font, no remote call), so it works offline; it holds no data and no token
    ✓ Handlers run in a thread pool, so a slow model call never blocks the others
    ✓ POST /v1/diagnose takes a heal request plus prior_heals under the heal route's limits and refusals; with the setting off it
      answers 404 with {"status": "needs_human", "reason": "diagnose_off"} and reads nothing; GET /v1/heals also filters on
      confidence, patch_suggested and downgraded

Environment Variables:
    None here (HEAL_SERVICE_TOKEN is read by config.ServiceSettings and passed in).

Author: PMAC
===============================================================================
"""
import hmac
import math
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from . import __version__
from .apidocs import install as install_api_page
from .core import DIAGNOSE_OFF, Busy, DiagnoseOff, HealClosed, HealSession, NotHealed, RepoMismatch, UnknownHeal
from .dashboard import page as dashboard_page
from .export import csv_chunks
from .metrics import aggregate
from .schemas import (LIMITS, FollowupRequest, LimitExceeded, RefusedRequest, ResultRequest, parse_audit_request,
                      parse_diagnose_request, parse_request)
from .store import MAX_AGG_ROWS, SORTS, FilterError

MAX_RESULT_BYTES = 64 * 1024
CSV_PAGE = 1000                       # rows one CSV chunk reads; the store lock is released between chunks
MAX_INT = 2 ** 31                     # the largest number a numeric filter takes
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MAX_JSON_ROWS = 1000                  # rows one JSON page may hold; a CSV export may hold MAX_AGG_ROWS


def _errors(e: ValidationError) -> list:
    """Validation errors as {loc, msg}: where and which rule, never the offending input."""
    return [{"loc": [str(p) for p in err["loc"]], "msg": err["msg"]} for err in e.errors()]


async def _body(request: Request, limit: int) -> bytes:
    """The request body, refusing (413) as soon as it is known to be over `limit`: by Content-Length first, then while
    streaming, so an over-size body is never read to the end."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise LimitExceeded(f"the request is over {limit // 1024} KB")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise LimitExceeded(f"the request is over {limit // 1024} KB")
        chunks.append(chunk)
    return b"".join(chunks)


def _when(value: Optional[str], name: str) -> Optional[float]:
    """A time filter as epoch seconds: a number, or an ISO 8601 date or time (UTC when it names no zone)."""
    if value is None:
        return None
    try:
        number = float(value)
        if not math.isfinite(number):
            raise HTTPException(422, detail=f"{name} must be a finite number of seconds or an ISO 8601 date")
        return number
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, detail=f"{name} must be epoch seconds or an ISO 8601 date") from None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _host_of(header: str) -> str:
    """The host in a Host header without its port: `localhost:8765` -> localhost, `[::1]:8765` -> [::1]. A header that is not
    `host`, `host:port` or `[v6]` / `[v6]:port` (anything after the `]` but a port) gives "", which no allow-list holds."""
    host = header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        rest = host[end + 1:] if end > 0 else "?"
        if rest != "" and not (rest.startswith(":") and rest[1:].isdigit()):
            return ""
        return host[:end + 1]
    if host.count(":") == 1:
        name, port = host.rsplit(":", 1)
        return name if port.isdigit() else ""
    return host


def create_app(core: HealSession, token: Optional[str] = None, bind_host: Optional[str] = None) -> FastAPI:
    """The API over `core`. `token` defaults to the core's settings; without one the API is open, and then a request whose
    Host header is not loopback (or the address the server was told to bind, `bind_host`) is refused, so a web page cannot reach
    an open loopback service through a rebound DNS name. Every POST must say it carries JSON."""
    token = token if token is not None else core.settings.token
    app = FastAPI(title="voyageur", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

    allowed_hosts = LOOPBACK_HOSTS | ({bind_host.strip().lower()} if bind_host else set())
    if bind_host and ":" in bind_host and not bind_host.startswith("["):
        allowed_hosts.add(f"[{bind_host.strip().lower()}]")

    @app.middleware("http")
    async def _guard(request: Request, call_next: Any) -> Response:
        if not token and _host_of(request.headers.get("host", "")) not in allowed_hosts:
            return JSONResponse({"detail": "this open service answers only to a loopback host name"}, status_code=403)
        if request.method == "POST" and not request.headers.get("content-type", "").lower().startswith("application/json"):
            return JSONResponse({"detail": "a POST must carry Content-Type: application/json"}, status_code=415)
        return await call_next(request)

    @app.exception_handler(LimitExceeded)
    async def _too_big(_: Request, e: LimitExceeded) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=413)

    @app.exception_handler(RefusedRequest)
    async def _refused(_: Request, e: RefusedRequest) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(FilterError)
    async def _bad_filter(_: Request, e: FilterError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(ValidationError)
    async def _invalid(_: Request, e: ValidationError) -> JSONResponse:
        return JSONResponse({"detail": _errors(e)}, status_code=422)

    def auth(request: Request) -> None:
        if not token:
            return
        given = request.headers.get("authorization", "")
        scheme, _, value = given.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.encode(), token.encode()):
            raise HTTPException(401, detail="a bearer token is required", headers={"WWW-Authenticate": "Bearer"})

    guarded = [Depends(auth)]

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/v1/capabilities", dependencies=guarded)
    def capabilities() -> Dict[str, Any]:
        return core.capabilities()

    @app.get("/v1/probe.js", dependencies=guarded)
    def probe() -> Response:
        text = (resources.files("voyageur") / "probe" / "probe.js").read_text(encoding="utf-8")
        return Response(text, media_type="application/javascript")

    @app.post("/v1/heal", dependencies=guarded)
    async def heal(request: Request) -> Dict[str, Any]:
        req = parse_request(await _body(request, LIMITS["request_bytes"]))
        return (await run_in_threadpool(core.start, req)).to_json()

    @app.post("/v1/heals/{heal_id}/result", dependencies=guarded)
    async def result(heal_id: str, request: Request) -> Dict[str, Any]:
        body = await _body(request, MAX_RESULT_BYTES)
        try:
            res = ResultRequest.model_validate_json(body)
        except ValidationError as e:
            return JSONResponse({"detail": _errors(e)}, status_code=422)       # type: ignore[return-value]
        try:
            return (await run_in_threadpool(core.result, heal_id, res)).to_json()
        except UnknownHeal:
            raise HTTPException(404, detail="no such heal") from None
        except HealClosed:
            raise HTTPException(409, detail="this heal already has its final answer") from None

    @app.get("/v1/heals/{heal_id}", dependencies=guarded)
    def record(heal_id: str) -> Dict[str, Any]:
        rec = core.get(heal_id)
        if rec is None:
            raise HTTPException(404, detail="no such heal")
        return rec

    @app.post("/v1/heals/{heal_id}/followup", dependencies=guarded)
    async def followup(heal_id: str, request: Request) -> Dict[str, Any]:
        body = await _body(request, MAX_RESULT_BYTES)
        try:
            req = FollowupRequest.model_validate_json(body)
        except ValidationError as e:
            return JSONResponse({"detail": _errors(e)}, status_code=422)       # type: ignore[return-value]
        try:
            return await run_in_threadpool(core.followup, heal_id, req)
        except UnknownHeal:
            raise HTTPException(404, detail="no such heal") from None
        except NotHealed:
            raise HTTPException(409, detail="only a heal that ended healed takes a follow-up") from None
        except RepoMismatch:
            raise HTTPException(409, detail="the repo or run in the follow-up is not the one of the heal record") from None

    @app.post("/v1/audit", dependencies=guarded)
    async def audit(request: Request) -> Dict[str, Any]:
        req = parse_audit_request(await _body(request, LIMITS["request_bytes"]))
        try:
            return await run_in_threadpool(core.audit, req)
        except Busy:
            raise HTTPException(429, detail="too many audits are running; retry shortly", headers={"Retry-After": "2"}) from None

    @app.post("/v1/diagnose", dependencies=guarded)
    async def diagnose(request: Request) -> Any:
        off = JSONResponse({"status": "needs_human", "reason": "diagnose_off", "why": DIAGNOSE_OFF}, status_code=404)
        if not core.settings.diagnose_enabled:
            return off                                # nothing is read, parsed or recorded
        req = parse_diagnose_request(await _body(request, LIMITS["request_bytes"]))
        try:
            return (await run_in_threadpool(core.diagnose, req)).to_json()
        except DiagnoseOff:
            return off

    @app.get("/v1/heals/{heal_id}/overlay.png", dependencies=guarded)
    def overlay(heal_id: str) -> Response:
        png = core.store.get_overlay(heal_id)
        if png is None:
            raise HTTPException(404, detail="no overlay for this heal")
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, no-store",
                                                             "X-Content-Type-Options": "nosniff"})

    @app.get("/v1/heals", dependencies=guarded)
    def heals(repo: Optional[str] = None, framework: Optional[str] = None, language: Optional[str] = None,
              outcome: Optional[str] = None, reason: Optional[str] = None, settled_at: Optional[int] = Query(None, ge=0, le=MAX_INT),
              ended_at: Optional[int] = Query(None, ge=0, le=MAX_INT), tier_movement: Optional[str] = None, page_language: Optional[str] = None,
              test_id: Optional[str] = None, file: Optional[str] = None, locator_key: Optional[str] = None,
              run: Optional[str] = None, run_id: Optional[str] = None, pr_state: Optional[str] = None,
              mode: Optional[str] = None, model: Optional[str] = None, role: Optional[str] = None,
              local: Optional[str] = None, with_calls: Optional[str] = None, led_to_fix: Optional[str] = None,
              wrong_element: Optional[str] = None, budget_exhausted: Optional[str] = None,
              cache_hit: Optional[str] = None, branch: Optional[str] = None, failed_again: Optional[str] = None,
              drift: Optional[str] = None, durability: Optional[str] = None, confidence: Optional[str] = None,
              patch_suggested: Optional[str] = None, downgraded: Optional[str] = None, sort: str = "newest",
              format: str = "json", from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
              limit: int = Query(100, ge=1, le=MAX_AGG_ROWS), offset: int = Query(0, ge=0, le=MAX_INT)) -> Any:
        if format not in ("json", "csv"):
            raise HTTPException(422, detail="format must be json or csv")
        if sort not in SORTS:
            raise HTTPException(422, detail=f"sort must be one of {' | '.join(SORTS)}")
        if format == "json" and limit > MAX_JSON_ROWS:
            raise HTTPException(422, detail=f"limit is at most {MAX_JSON_ROWS} for json (a csv export takes up to {MAX_AGG_ROWS})")
        found = dict(
            repo=repo, framework=framework, language=language, outcome=outcome, reason=reason, settled_at=settled_at,
            ended_at=ended_at, tier_movement=tier_movement, page_language=page_language, test_id=test_id, file=file,
            locator_key=locator_key, run_id=run_id if run_id is not None else run, pr_state=pr_state, mode=mode,
            model=model, role=role, local=local, with_calls=with_calls, led_to_fix=led_to_fix,
            wrong_element=wrong_element, budget_exhausted=budget_exhausted, cache_hit=cache_hit, branch=branch,
            failed_again=failed_again, drift=drift, durability=durability, confidence=confidence,
            patch_suggested=patch_suggested, downgraded=downgraded)
        since, until = _when(from_, "from"), _when(to, "to")
        if format == "csv":
            core.store.count_heals(since, until, **found)            # a bad filter is a 422 now, not an error mid-stream

            def page(at: int, n: int) -> list:
                return core.store.list_heals(since=since, until=until, limit=n, offset=offset + at, sort=sort, **found)
            return StreamingResponse(csv_chunks(page, limit, CSV_PAGE), media_type="text/csv; charset=utf-8",
                                     headers={"Content-Disposition": 'attachment; filename="heals.csv"',
                                              "X-Content-Type-Options": "nosniff"})
        rows = core.store.list_heals(since=since, until=until, limit=limit, offset=offset, sort=sort, **found)
        total = core.store.count_heals(since, until, **found)
        for row in rows:
            row["links"] = {"self": f"/v1/heals/{row['heal_id']}"}
        return {"heals": rows, "count": len(rows), "total": total, "limit": limit, "offset": offset}

    @app.get("/v1/metrics", dependencies=guarded)
    def metrics(repo: Optional[str] = None, framework: Optional[str] = None, language: Optional[str] = None,
                page_language: Optional[str] = None, run: Optional[str] = None, run_id: Optional[str] = None,
                from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None) -> Dict[str, Any]:
        return aggregate(core.store, core.settings.prices, _when(from_, "from"), _when(to, "to"), repo=repo,
                         framework=framework, language=language, page_language=page_language,
                         run_id=run_id if run_id is not None else run)

    @app.get("/metrics", dependencies=guarded)
    def prometheus() -> Response:
        text = core.telemetry.render(__version__, core.open_conversations())
        return Response(text, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard() -> Response:
        html, headers = dashboard_page()
        return Response(html, media_type="text/html; charset=utf-8", headers=headers)


    install_api_page(app)
    return app
