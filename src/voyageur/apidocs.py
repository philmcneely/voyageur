"""
===============================================================================
API page (GET /docs and GET /openapi.json: the contract, and a place to run it)
===============================================================================
The OpenAPI document of this service and Swagger UI over it, with "Try it out" on, so a person can send the sample requests
to the service they are running and read the answers. Everything is served from package data: no CDN, no web font, nothing
fetched from anywhere else, so the page works with no network, like the dashboard.

    the bodies     the POST routes read their body themselves (size limits first, then the strict models), so FastAPI
                   cannot see them; the document is given each model's JSON schema and the examples by hand here
    the examples   copies of the contract fixtures (fixtures/contract/<case>/request.json) with the case's own description;
                   a unit test fails when a copy and its fixture differ. The first one settles from the passing run's
                   fingerprint with no model call, so it heals on a service with no model configured
    served without a token
                   the page, its assets and the document hold no data and no secret (the same reason as the dashboard);
                   every call the page makes to the API carries the token typed into "Authorize", kept in memory only

Features:
    ✓ install(app): adds /docs, its three assets and /openapi.json to the app create_app builds
    ✓ Bearer security scheme on every route that needs the token, none on /healthz
    ✓ Content-Security-Policy: scripts and styles from this service only ('unsafe-inline' for styles, which Swagger UI sets
      on elements), connect-src 'self', no frames, no forms, no base tag; plus nosniff and no-referrer
    ✓ Swagger UI 5.17.14 (swagger-ui-dist, Apache-2.0: SWAGGER-UI-LICENSE and SWAGGER-UI-NOTICE beside it)

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import json
from functools import lru_cache
from importlib import resources
from typing import Any, Dict, List, Type

from fastapi import FastAPI, HTTPException
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from . import __version__
from .schemas import (AuditRequest, DiagnoseRequest, DiagnoseResponse, FollowupRequest, HealRequest, HealResponse,
                      OverlayRequest, OverlayResponse, ResultRequest)

OVERLAY_EXAMPLES = ["overlay", "overlay_every_element"]
ASSETS = {"swagger-ui-bundle.js": "application/javascript", "swagger-ui.css": "text/css", "init.js": "application/javascript"}
EXAMPLES = ["fingerprint_match_zero_tokens", "renamed_id_to_testid", "target_removed_needs_human", "not_drift",
            "prompt_injection_in_page_text"]
OPEN_PATHS = {"/healthz"}
HEADERS = {"Content-Security-Policy": "; ".join([
    "default-src 'none'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data:",
    "font-src 'self' data:", "connect-src 'self'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"]),
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}

DESCRIPTION = """Repairs drifted Playwright locators from a text-only page inventory. The service writes every locator
itself; a model, when one is needed, only names an element from a quoted list.

**A heal is a short conversation.** `POST /v1/heal` answers `try` with a candidate locator, or a decision (`healed`,
`needs_human`, `not_drift`). The client tries the candidate in the live page, re-runs the exact test and reports what it saw
to `POST /v1/heals/{heal_id}/result`; only a verified result counts as a heal.

**To try it here:** open `POST /v1/heal`, keep the example *fingerprint match zero tokens* (it needs no model), Execute, copy
the `heal_id` from the answer, then send the result example to `POST /v1/heals/{heal_id}/result`. With a token configured,
press Authorize first. Full examples and every rule: docs/API.md in the repository."""

RESULT_EXAMPLES = {
    "passed": {"summary": "the candidate matched one element and the test passed",
               "value": {"count": 1, "hit": {"ref": "e0"}, "test_outcome": "passed"}},
    "wrong_count": {"summary": "the candidate matched two elements in the live page",
                    "value": {"count": 2}},
    "failed": {"summary": "one element, but the re-run failed at the same line",
               "value": {"count": 1, "hit": {"ref": "e0"}, "test_outcome": "failed"}},
}
FOLLOWUP_EXAMPLE = {"summary": "the heal's PR merged unchanged, still passing",
                    "value": {"repo": "", "pr_state": "merged_unchanged", "failed_again": False, "runs_since": 12,
                              "days_since": 9}}


def _asset(name: str) -> bytes:
    return (resources.files("voyageur") / "apidocs" / name).read_bytes()


@lru_cache(maxsize=1)
def examples() -> Dict[str, Dict[str, Any]]:
    """The heal request examples, keyed by case name, each {summary, description, value} (fixture path dropped)."""
    out = {}
    for name in EXAMPLES:
        data = json.loads(_asset(f"examples/{name}.json"))
        out[name] = {"summary": data["summary"], "description": data["description"], "value": data["value"]}
    return out


def _body(model: Type[BaseModel], examples_: Dict[str, Any], schemas: Dict[str, Any]) -> Dict[str, Any]:
    schema = model.model_json_schema(ref_template="#/components/schemas/{model}", by_alias=True)
    schemas.update(schema.pop("$defs", {}))
    schemas[model.__name__] = schema
    return {"required": True, "content": {"application/json": {
        "schema": {"$ref": f"#/components/schemas/{model.__name__}"}, "examples": examples_}}}


def _audit_example() -> Dict[str, Any]:
    heal = examples()["renamed_id_to_testid"]["value"]
    return {"from_the_renamed_id_case": {"summary": "the files and page of the renamed id case, no failure",
                                         "value": {"source": heal["source"], "page": heal["page"]}}}


def _overlay_examples() -> Dict[str, Any]:
    """The overlay examples: a page with a small screenshot and some locators, and the same page with none (every element)."""
    out = {}
    for name in OVERLAY_EXAMPLES:
        data = json.loads(_asset(f"examples/{name}.json"))
        out[name] = {"summary": data["summary"], "description": data["description"], "value": data["value"]}
    return out


def _diagnose_example() -> Dict[str, Any]:
    heal = examples()["not_drift"]["value"]
    return {"not_drift_no_prior_heals": {"summary": "an assertion failure with every locator still matching",
                                         "value": {**heal, "prior_heals": []}}}


def _document(routes: List[Any]) -> Dict[str, Any]:
    doc = get_openapi(title="voyageur", version=__version__, description=DESCRIPTION, routes=routes)
    components = doc.setdefault("components", {})
    schemas: Dict[str, Any] = components.setdefault("schemas", {})
    components["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer",
                                                "description": "HEAL_SERVICE_TOKEN; not needed when the service runs with none"}}
    bodies = {("/v1/heal", "post"): (HealRequest, examples()),
              ("/v1/heals/{heal_id}/result", "post"): (ResultRequest, RESULT_EXAMPLES),
              ("/v1/heals/{heal_id}/followup", "post"): (FollowupRequest, {"merged": FOLLOWUP_EXAMPLE}),
              ("/v1/audit", "post"): (AuditRequest, _audit_example()),
              ("/v1/overlay", "post"): (OverlayRequest, _overlay_examples()),
              ("/v1/diagnose", "post"): (DiagnoseRequest, _diagnose_example())}
    answers = {("/v1/heal", "post"): HealResponse, ("/v1/heals/{heal_id}/result", "post"): HealResponse,
               ("/v1/overlay", "post"): OverlayResponse, ("/v1/diagnose", "post"): DiagnoseResponse}
    for path, ops in doc.get("paths", {}).items():
        for method, op in ops.items():
            if (path, method) in bodies:
                model, ex = bodies[(path, method)]
                op["requestBody"] = _body(model, ex, schemas)
            if (path, method) in answers:
                model = answers[(path, method)]
                _body(model, {}, schemas)                                   # registers the schema and its parts
                op["responses"]["200"]["content"] = {"application/json": {"schema": {"$ref": f"#/components/schemas/{model.__name__}"}}}
            if path not in OPEN_PATHS:
                op["security"] = [{"bearer": []}]
    return doc


def install(app: FastAPI) -> None:
    """Add the API page, its assets and the OpenAPI document to `app` (after its routes are defined)."""
    routes: List[Any] = []
    built: List[Dict[str, Any]] = []                   # the document, built on the first request and kept for this app

    @app.get("/openapi.json", include_in_schema=False)
    def openapi() -> JSONResponse:
        if not built:
            built.append(_document(routes))
        return JSONResponse(built[0], headers={"X-Content-Type-Options": "nosniff"})

    @app.get("/docs", include_in_schema=False)
    def docs() -> Response:
        return Response(_asset("index.html"), media_type="text/html; charset=utf-8", headers=HEADERS)

    @app.get("/docs/{name}", include_in_schema=False)
    def asset(name: str) -> Response:
        if name not in ASSETS:
            raise HTTPException(404, detail="no such file")
        return Response(_asset(name), media_type=ASSETS[name], headers={**HEADERS, "Cache-Control": "public, max-age=3600"})

    routes.extend(r for r in app.routes if getattr(r, "include_in_schema", False))
