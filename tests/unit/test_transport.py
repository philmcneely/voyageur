"""Unit: one model call against a mocked endpoint (no network): egress, failures as reasons, answer-only replies."""
import json

import httpx
import pytest

from voyageur import redact
from voyageur.llm import Endpoint, Usage
from voyageur.transport import Transport, chat_text, strip_fences, strip_thinking

pytestmark = pytest.mark.unit

PNG = b"\x89PNG\r\n\x1a\nfake"
EP = Endpoint(base_url="http://127.0.0.1:8080/v1", model="m", api_key="sk-TRANSPORT-KEY-1")


class Server:
    """A mock endpoint: records each request and answers with the queued replies (an Exception raises, an int is a status)."""

    def __init__(self, *replies):
        self.replies = list(replies) or ["page.get_by_label('x')"]
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        if isinstance(r, int):
            return httpx.Response(r, text="nope")
        if isinstance(r, dict):
            return httpx.Response(200, json=r)
        if isinstance(r, bytes):
            return httpx.Response(200, content=r)
        return httpx.Response(200, json={"choices": [{"message": {"content": r}}],
                                         "usage": {"prompt_tokens": 11, "completion_tokens": 3}})

    def transport(self):
        return Transport(httpx.Client(transport=httpx.MockTransport(self)))


@pytest.fixture(autouse=True)
def _no_registered_secrets():
    redact._SECRETS.clear()
    yield
    redact._SECRETS.clear()


def test_an_answer_comes_back_with_its_usage_as_integers():
    s = Server()
    got = s.transport().call(EP, "which one?", heal_id="h-1")
    assert got.text == "page.get_by_label('x')" and got.sent and got.why == ""
    assert got.usage == Usage(11, 3, 0) and got.duration_ms >= 0
    req = s.requests[0]
    assert req.headers["x-heal-id"] == "h-1" and req.headers["x-heal-caller"] == "voyageur"
    assert req.headers["authorization"] == "Bearer sk-TRANSPORT-KEY-1"
    assert json.loads(req.content)["messages"][0]["content"] == "which one?"


def test_an_unconfigured_endpoint_sends_nothing():
    s = Server()
    got = s.transport().call(Endpoint(), "p")
    assert got.text is None and "not configured" in got.why and not got.sent and not s.requests


@pytest.mark.parametrize("url", ["http://203.0.113.9/v1", "http://example.org/v1"])
def test_page_text_goes_over_plain_http_only_to_a_private_host(url):
    s = Server()
    got = s.transport().call(Endpoint(base_url=url, model="m"), "page text")
    assert got.text is None and "https" in got.why and not got.sent and not s.requests


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/v1", "http://localhost:8080/v1", "http://10.0.0.2/v1",
                                 "https://api.example.com/v1"])
def test_private_http_or_any_https_is_allowed(url):
    got = Server().transport().call(Endpoint(base_url=url, model="m"), "p")
    assert got.text == "page.get_by_label('x')"


def test_a_malformed_base_url_is_a_reason_not_a_crash():
    got = Server().transport().call(Endpoint(base_url="http://[::1/v1", model="m"), "p")
    assert got.text is None and got.why and not got.sent


def test_an_image_without_permission_is_never_built_into_a_request():
    s = Server()
    got = s.transport().call(EP, "p", [("Image 1: a.png", PNG)])
    assert got.text is None and "ImagesNotAllowed" in got.why and not s.requests


def test_an_http_error_is_recorded_as_the_status_alone():
    got = Server(503).transport().call(EP, "p")
    assert got.text is None and got.why == "HTTP 503" and got.sent and "127.0.0.1" not in got.why


def test_a_network_error_is_a_scrubbed_reason():
    err = httpx.ConnectError("boom Bearer sk-TRANSPORT-KEY-1 at https://u:p@h/v1?t=9")
    got = Server(err).transport().call(EP, "p")
    assert got.text is None and "ConnectError" in got.why
    assert "TRANSPORT-KEY" not in got.why and "u:p" not in got.why and "t=9" not in got.why


def test_the_endpoint_credentials_are_registered_for_later_scrubs():
    Server().transport().call(EP, "p")
    assert "TRANSPORT-KEY" not in redact.scrub_secrets("later error with sk-TRANSPORT-KEY-1 in it")


@pytest.mark.parametrize("reply, why", [(b"<html>", "not JSON"), ({"choices": []}, "choices[0].message"),
                                        ("   ", "empty"), ("<think>no end", "empty"),
                                        (b"x" * (2 * 1024 * 1024 + 1), "too large")])
def test_unusable_replies_are_reasons_and_keep_their_usage(reply, why):
    got = Server(reply).transport().call(EP, "p")
    assert got.text is None and why in got.why and got.sent


def test_usage_survives_an_empty_answer():
    got = Server("<think>x</think>").transport().call(EP, "p")
    assert got.text is None and got.usage == Usage(11, 3, 0)


def test_a_client_passed_in_is_not_closed_by_the_transport():
    client = httpx.Client(transport=httpx.MockTransport(Server()))
    with Transport(client):
        pass
    assert not client.is_closed
    own = Transport()
    own.close()
    assert own.client.is_closed


def test_the_client_it_creates_ignores_proxy_environment_variables(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    t = Transport()
    try:
        assert t.client._trust_env is False
    finally:
        t.close()


def test_thinking_fences_and_stray_closers_are_stripped():
    assert strip_thinking("<think>plan</think>  answer ") == "answer"
    assert strip_thinking("plan</think>answer") == "answer"
    assert strip_thinking("<think>never closed") is None
    assert strip_fences("```python\nx\n```") == "x"
    assert chat_text("<think>a</think>```\ne1\n```") == "e1"
    assert chat_text(None) is None and chat_text("<think>a</think>") is None


@pytest.mark.parametrize("body", [b"[" * 100_000 + b"]" * 100_000, b'{"a":' * 50_000 + b"1" + b"}" * 50_000])
def test_a_deeply_nested_reply_is_a_reason_not_a_crash(body):
    res = Server(body).transport().call(EP, "p")
    assert res.text is None and res.sent and "not JSON" in res.why
