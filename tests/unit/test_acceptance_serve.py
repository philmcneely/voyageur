"""
===============================================================================
Acceptance HTTP server tests (the in-process service the TypeScript client talks to)
===============================================================================
`--client ts` serves the in-process app over HTTP. The port is bound once and that socket is handed to uvicorn, so no other
process can take the port between choosing it and serving on it, and a server that does not start is an error, not a URL that
answers nothing.

Author: PMAC
===============================================================================
"""
import importlib.util
import socket
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.unit

_SPEC = importlib.util.spec_from_file_location("run_acceptance", Path(__file__).parents[2] / "testbed" / "run_acceptance.py")
acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acceptance)


def test_the_server_answers_on_the_socket_it_was_handed():
    svc = acceptance.Service("fake", None)
    url = svc.serve_http()
    try:
        assert httpx.get(url + "/healthz", timeout=5).status_code == 200
        assert svc.serve_http() == url                                      # served once
    finally:
        svc.http_server.should_exit = True


def test_uvicorn_gets_the_bound_socket_not_a_port_to_bind_again(monkeypatch):
    import uvicorn
    handed = []

    class Recorder:
        def __init__(self, config):
            self.started = False

        def run(self, sockets=None):
            handed.append(sockets)
            self.started = True

    monkeypatch.setattr(uvicorn, "Server", Recorder)
    url = acceptance.Service("fake", None).serve_http()
    (sock,) = handed[0]
    assert isinstance(sock, socket.socket) and url.endswith(f":{sock.getsockname()[1]}")
    sock.close()


def test_a_server_that_never_starts_is_an_error(monkeypatch):
    import uvicorn

    class Stalled:
        def __init__(self, config):
            self.started = False

        def run(self, sockets=None):
            pass

    monkeypatch.setattr(uvicorn, "Server", Stalled)
    monkeypatch.setattr(acceptance, "SERVE_START_S", 0.2)
    with pytest.raises(RuntimeError, match="did not start"):
        acceptance.Service("fake", None).serve_http()
