"""
===============================================================================
MCP Server (heal_locator, audit_locators and diagnose_failure over stdio, on the same core)
===============================================================================
`voyageur mcp` lets an MCP client (an agent such as Playwright's Test Agents healer, or any editor that speaks the
Model Context Protocol) ask the service about locators instead of guessing. Two tools, and a third when the operator turns
diagnose on (HEAL_DIAGNOSE), each a thin shell over HealSession:

    heal_locator     The body of POST /v1/heal (the failure, the source files, the page inventory the probe collects, an
                     optional last_pass). Runs the heal ladder in advise mode and returns the service's decision: `advised`
                     with the best locator (code, strategy, tier, why, alternatives), or `needs_human` / `not_drift` with a
                     reason. It never returns a patch and never edits a file: the agent decides what to do with it.
    audit_locators   The body of POST /v1/audit (the source files and the page inventory). Returns every locator call ranked
                     against the page: its tier, whether it is unique, and a more stable locator when one is unique.
    diagnose_failure The body of POST /v1/diagnose (a heal request plus `prior_heals`). For a failure that is not locator drift:
                     returns a cause from a closed list with evidence, a confidence and at most one suggested edit that is
                     not applied. Listed only when HEAL_DIAGNOSE is on; called with it off it returns the diagnose_off refusal.

The tool input is the API's own request body, validated by the same code: size and file limits, the refusal of input values,
cookies and storage dumps, then the same pydantic models (schemas.parse_request, schemas.parse_audit_request,
schemas.parse_diagnose_request). A request the
API would refuse is an error result here with the same sentence, never an echo of the input.

Features:
    ✓ Optional dependency: the official `mcp` SDK (pip install "voyageur[mcp]"); nothing here is imported until
      `voyageur mcp` runs, so the service itself needs no MCP package
    ✓ Written and tested against mcp 2.x only (mcp.server.mcpserver.MCPServer); the extra is pinned to mcp>=2.3,<3
    ✓ heal_locator forces options.mode to advise whatever the caller sent: this surface can never start a try conversation
    ✓ The tool functions are plain functions of (core, arguments), so they are tested without a transport
    ✓ The model call and the CPU-heavy stages run off the event loop (a thread), exactly as the HTTP handlers do
    ✓ Stdout belongs to the protocol: nothing else is printed there; messages go to stderr

Environment Variables:
    The same HEAL_* settings as `voyageur serve` (HEAL_DB, HEAL_TEXT_BASE_URL / MODEL / API_KEY, HEAL_RUN_TOKEN_BUDGET ...).
    There is no bearer token: a stdio server is reached only by the process that started it.

Author: PMAC
===============================================================================
"""
import asyncio
from typing import Any, Dict, Mapping

from pydantic import ValidationError

from .core import DIAGNOSE_OFF, Busy, DiagnoseOff, HealSession
from .schemas import LimitExceeded, RefusedRequest, parse_audit_request, parse_diagnose_request, parse_request

HEAL_DESCRIPTION = (
    "Ask the heal service which element a broken Playwright locator meant and how to write it better. `request` is the "
    "body of POST /v1/heal: client {framework, language}, test {id}, failure {error, stack}, source {roots, files "
    "[{path, content}]} holding the file(s) of the failing call, page {url, inventory (from the service's probe.js), "
    "aria_snapshot}, optional last_pass {fingerprint}. Returns status `advised` with advice {code, strategy, tier, "
    "tier_movement, why, alternatives}, or `needs_human` / `not_drift` with a reason. Advice only: no patch is returned and "
    "nothing is edited. Input values, cookies and storage are refused.")
AUDIT_DESCRIPTION = (
    "Rank every page.locator / getBy* call in some source files against a page inventory, with no model call. `request` is "
    "the body of POST /v1/audit: source {roots, files [{path, content}]}, page {url, inventory (from the service's "
    "probe.js), aria_snapshot}, optional client {language}. Returns, per locator, its kind and tier, how many listed "
    "elements it matches, a status (ok, upgrade, ambiguous, missing, unknown) and, for `upgrade`, the more stable unique "
    "locator the service would write.")
DIAGNOSE_DESCRIPTION = (
    "Ask the heal service what most likely caused a Playwright failure that is NOT a locator that stopped matching (a wrong "
    "expectation, a page that misbehaves, a step that came too early). `request` is the body of POST /v1/diagnose: the body of "
    "POST /v1/heal plus optional prior_heals [{heal_id, file, line, from, to}] for the locators earlier runs rewrote. Returns "
    "status `diagnosed` with a diagnosis {cause, evidence, confidence, revert_heal_id, suggested_patch}, `not_applicable` for "
    "locator drift (use heal_locator), or `needs_human`. Advice only: nothing is edited and the suggested edit is never applied. "
    "Evidence and notes are the model's own words, untrusted. Input values, cookies and storage are refused.")


class ToolRefused(ValueError):
    """The arguments are not acceptable, or the work was stopped. The message says which rule, never what was sent."""


def _refusal(e: Exception) -> ToolRefused:
    if isinstance(e, ValidationError):
        return ToolRefused("the request does not match the schema: " + "; ".join(
            ".".join(str(p) for p in err["loc"]) + " " + err["msg"] for err in e.errors()[:8]))
    return ToolRefused(str(e))


def heal_locator(core: HealSession, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    """Advise on one failure: the POST /v1/heal body with options.mode forced to advise. Returns the response JSON."""
    if not isinstance(arguments, Mapping):
        raise ToolRefused("the request must be a JSON object")
    body = dict(arguments)
    options = body.get("options")
    if options is not None and not isinstance(options, Mapping):
        raise ToolRefused("options must be a JSON object")
    body["options"] = {**(options or {}), "mode": "advise"}
    try:
        req = parse_request(body)
    except (LimitExceeded, RefusedRequest, ValidationError) as e:
        raise _refusal(e) from None
    return core.start(req).to_json()


def audit_locators(core: HealSession, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    """Rank the locators of the sent files against the inventory (the POST /v1/audit body)."""
    try:
        req = parse_audit_request(dict(arguments) if isinstance(arguments, Mapping) else arguments)
    except (LimitExceeded, RefusedRequest, ValidationError) as e:
        raise _refusal(e) from None
    try:
        return core.audit(req)
    except Busy:
        raise ToolRefused("too many audits are running; retry shortly") from None


def diagnose_failure(core: HealSession, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    """Diagnose one failure (the POST /v1/diagnose body). With diagnose off the result is the refusal, not an error."""
    try:
        req = parse_diagnose_request(dict(arguments) if isinstance(arguments, Mapping) else arguments)
    except (LimitExceeded, RefusedRequest, ValidationError) as e:
        raise _refusal(e) from None
    try:
        return core.diagnose(req).to_json()
    except DiagnoseOff:
        return {"status": "needs_human", "reason": "diagnose_off", "why": DIAGNOSE_OFF}


def build_server(core: HealSession) -> Any:
    """The MCP server with its tools bound to `core` (diagnose_failure only when diagnose is on). Raises ImportError when the `mcp` package is not installed."""
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    server = MCPServer("voyageur", instructions="Locator advice, audit and failure diagnosis for Playwright "
                             "suites. Every tool takes the same request body as the voyageur HTTP API.")

    async def run(fn: Any, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(fn, core, request)
        except ToolRefused as e:
            raise ToolError(str(e)) from None

    @server.tool(name="heal_locator", description=HEAL_DESCRIPTION)
    async def heal_locator_tool(request: Dict[str, Any]) -> Dict[str, Any]:
        return await run(heal_locator, request)

    @server.tool(name="audit_locators", description=AUDIT_DESCRIPTION)
    async def audit_locators_tool(request: Dict[str, Any]) -> Dict[str, Any]:
        return await run(audit_locators, request)

    if core.settings.diagnose_enabled:
        @server.tool(name="diagnose_failure", description=DIAGNOSE_DESCRIPTION)
        async def diagnose_failure_tool(request: Dict[str, Any]) -> Dict[str, Any]:
            return await run(diagnose_failure, request)

    return server


def serve_stdio(core: HealSession) -> None:
    """Run the MCP server on stdin and stdout until the client closes them."""
    build_server(core).run("stdio")
