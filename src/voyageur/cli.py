"""
===============================================================================
Command Line (voyageur serve, voyageur heal, voyageur mcp)
===============================================================================
Three commands. `voyageur serve` runs the HTTP service. `voyageur heal --request req.json` runs one heal offline from a
saved request and prints the response JSON: the same core and the same rules as the API, with a store that lives only for the
command, which makes it the way to replay a recorded failure or to try a model setting without a client.

`voyageur mcp` runs the MCP server on stdin and stdout (tools heal_locator and audit_locators, mcp_server.py) for an agent
that speaks the Model Context Protocol. It needs the optional `mcp` package (pip install "voyageur[mcp]"); without it the
command exits 2 with that instruction.

The bind rules are enforced here, before anything listens: the service binds to 127.0.0.1 by default, and any other address
needs both --allow-remote and a HEAL_SERVICE_TOKEN, so an open API is never reachable from another machine by accident.

Features:
    ✓ serve: --host (default 127.0.0.1), --port (default 8765), --allow-remote; exit 2 with a message on a wide bind without
      the flag and a token
    ✓ heal: the same limits and refusals as the API (a value, a cookie or a storage dump is refused), exit 2 with a one-line
      message on an unreadable or unacceptable request, exit 0 with the response JSON otherwise (a decision is not an error)
    ✓ mcp: exit 2 with the install instruction when the `mcp` package is missing; stdout is the protocol, messages go to stderr
    ✓ Settings and the model come from the environment (HEAL_* variables; see config.py and llm.py)
    ✓ Nothing is printed but the response or a message: no file content, page text or model reply goes to the log

Environment Variables:
    HEAL_SERVICE_TOKEN, HEAL_DB, HEAL_RETENTION_DAYS, HEAL_RUN_TOKEN_BUDGET and the model settings; see config.py and llm.py.

Author: PMAC
===============================================================================
"""
import argparse
import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from pydantic import ValidationError

from .config import ConfigError, ServiceSettings
from .core import HealSession
from .llm import LLMConfigError
from .schemas import LimitExceeded, RefusedRequest, parse_request

DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 8765


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


# A reachable service has no rate limit on its bearer token, so the token itself must be too long to guess:
# 32 characters is what `python -c "import secrets; print(secrets.token_urlsafe(24))"` prints.
MIN_REMOTE_TOKEN = 32


def bind_problem(host: str, allow_remote: bool, token: Optional[str]) -> Optional[str]:
    """Why the service may not listen on `host`, or None. Anything but loopback needs --allow-remote and a token."""
    if _loopback(host):
        return None
    if not allow_remote:
        return f"binding to {host} needs --allow-remote (the default is {DEFAULT_HOST})"
    if not token:
        return f"binding to {host} needs HEAL_SERVICE_TOKEN to be set"
    if len(token) < MIN_REMOTE_TOKEN:
        return f"binding to {host} needs a HEAL_SERVICE_TOKEN of at least {MIN_REMOTE_TOKEN} characters"
    return None


# Idle kept-alive connections stay open this long. Clients reuse a socket after probing a page and re-running a test;
# uvicorn's default (5 s) sits just above Node fetch's own 4 s, so a busy client could reuse a socket the server had
# already closed and get ECONNRESET on its next call.
KEEP_ALIVE_S = 75


def _run_server(core: HealSession, host: str, port: int) -> None:
    import uvicorn
    from .app import create_app
    uvicorn.run(create_app(core, bind_host=host), host=host, port=port, log_level="info", timeout_keep_alive=KEEP_ALIVE_S)


def _serve(args: argparse.Namespace) -> int:
    settings = ServiceSettings.from_env(os.environ)
    problem = bind_problem(args.host, args.allow_remote, settings.token)
    if problem:
        print(f"voyageur: {problem}", file=sys.stderr)
        return 2
    _run_server(HealSession.from_env(os.environ), args.host, args.port)
    return 0


def _heal(args: argparse.Namespace) -> int:
    try:
        raw = Path(args.request).read_bytes()
    except OSError as e:
        print(f"voyageur: cannot read the request file ({type(e).__name__})", file=sys.stderr)
        return 2
    try:
        req = parse_request(raw)
    except (LimitExceeded, RefusedRequest) as e:
        print(f"voyageur: {e}", file=sys.stderr)
        return 2
    except ValidationError as e:
        print("voyageur: the request does not match the schema: "
              + "; ".join(".".join(str(p) for p in err["loc"]) + " " + err["msg"] for err in e.errors()[:5]),
              file=sys.stderr)
        return 2
    core = HealSession.from_env(os.environ, db_path=args.db or ":memory:")
    print(json.dumps(core.start(req).to_json(), indent=2))
    return 0


def _mcp(args: argparse.Namespace) -> int:
    try:
        from .mcp_server import serve_stdio
        import mcp  # noqa: F401  (the SDK itself: its absence is reported here, not as a traceback later)
    except ImportError:
        print('voyageur: the MCP server needs the optional package: pip install "voyageur[mcp]"', file=sys.stderr)
        return 2
    serve_stdio(HealSession.from_env(os.environ))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="voyageur", description="Repair drifted Playwright locators.")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the HTTP service")
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--allow-remote", action="store_true", help="allow a bind address other than loopback (needs a token). The service speaks plain HTTP: "
                       "put it behind a TLS reverse proxy, because the bearer token and the page inventory "
                       "would otherwise cross the network in the clear")
    serve.set_defaults(run=_serve)
    heal = sub.add_parser("heal", help="run one heal offline from a saved request and print the response")
    heal.add_argument("--request", required=True, help="path of a POST /v1/heal request body")
    heal.add_argument("--db", default=None, help="keep the record in this SQLite file (default: not kept)")
    heal.set_defaults(run=_heal)
    mcp_cmd = sub.add_parser("mcp", help="run the MCP server (tools heal_locator and audit_locators) on stdin and stdout")
    mcp_cmd.set_defaults(run=_mcp)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (ConfigError, LLMConfigError) as e:
        print(f"voyageur: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
