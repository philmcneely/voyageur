"""
===============================================================================
Model Transport (one model call: egress check, send, read only the answer)
===============================================================================
Sends one single-turn call to a resolved endpoint (llm.py builds the request and reads the reply) and returns a
CallResult. It never raises for anything the network or the provider can do: a refused destination, a timeout, an HTTP
error status, a body that is not JSON and an empty answer all come back as `text=None` with a short, scrubbed reason,
so the caller records it and moves on instead of crashing a heal.

Features:
    ✓ Egress policy on EVERY call (netpolicy.py): page text goes over plain http only to a loopback or private host.
      Checked per call, not cached, so a DNS change cannot outlive the check
    ✓ trust_env=False on the client it creates: an HTTP(S)_PROXY / ALL_PROXY in the environment would carry page text
      past the egress check
    ✓ A failed call is a reason, never an exception: an HTTP status is recorded as the status alone (httpx's own
      message holds the full request URL), any other error through redact.scrub_secrets
    ✓ The answer text only: leaked <think>...</think> is stripped (an unterminated one means the answer never
      arrived), code fences are removed, and reasoning fields are never read (llm.parse_reply)
    ✓ Usage is returned for every call that reached the provider, answer or not, as integers
    ✓ Every credential of the endpoint is registered with redact, so an error quoting one cannot leak it
    ✓ A client passed in stays the caller's to close; one created here is closed by close() / `with`
    ✓ No logging: what is worth recording about a call (outcome, usage, duration) is in the CallResult, and the
      caller decides where it goes

Environment Variables:
    None (endpoints come from llm.endpoint_from_env).

Author: PMAC
===============================================================================
"""
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import httpx

from .bounds import loads_bounded
from .llm import Endpoint, Image, Usage, build_request, parse_reply, usage_of
from .netpolicy import egress_refusal
from .redact import register_secret, scrub_secrets

EGRESS_WHAT = "model requests (page text and screenshots)"     # what the egress policy says is being sent
MAX_REASON_CHARS = 200                                          # a recorded failure reason is cut to this
MAX_RESPONSE_BYTES = 2 * 1024 * 1024                            # a reply larger than this is not read

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```[a-z]*\n?|```")


def strip_thinking(text: str) -> Optional[str]:
    """Drop leaked reasoning. Closed blocks are removed; a stray </think> keeps what follows it;
    an unterminated <think> means the answer never arrived (truncated) -> None."""
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:
        return None
    return text.strip()


def strip_fences(text: str) -> str:
    """The text without Markdown code-fence lines (``` and ```lang), trimmed."""
    return _FENCE.sub("", text).strip()


def chat_text(content: Optional[str]) -> Optional[str]:
    """The answer in a reply: reasoning and code fences removed; None when nothing is left."""
    text = strip_thinking(content or "")
    return strip_fences(text) if text else None


@dataclass
class CallResult:
    """What one call came to: the answer text (None when there is none), why not, and what it used."""
    text: Optional[str]
    why: str = ""
    usage: Usage = field(default_factory=Usage)
    duration_ms: int = 0
    sent: bool = False             # False: nothing left the machine (refused or not buildable)


class Transport:
    """Sends model calls through one HTTP client."""

    def __init__(self, client: Optional[httpx.Client] = None):
        self._owns_client = client is None
        self.client = client or httpx.Client(trust_env=False)

    def close(self) -> None:
        """Close the HTTP client, if this transport created it."""
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @staticmethod
    def egress_refusal(ep: Endpoint) -> str:
        """Why page text may not be sent to `ep`, or "". A malformed base URL is a refusal, never a crash."""
        try:
            return egress_refusal(ep.base, EGRESS_WHAT)
        except (httpx.InvalidURL, ValueError, TypeError) as e:
            return f"base URL unusable: {type(e).__name__}"

    def call(self, ep: Endpoint, prompt: str, images: Sequence[Image] = (), heal_id: str = "") -> CallResult:
        """One call to `ep`. Never raises for a network, provider or reply problem; see the module header."""
        if not ep.configured:
            return CallResult(None, "the endpoint is not configured (it needs a base URL and a model)")
        for secret in ep.secrets():
            register_secret(secret)
        refusal = self.egress_refusal(ep)
        if refusal:
            return CallResult(None, refusal)
        try:
            url, headers, body = build_request(ep, prompt, images, heal_id)
        except (ValueError, TypeError) as e:
            return CallResult(None, f"request not built: {type(e).__name__}")
        t0 = time.monotonic()

        def ms() -> int:
            return round((time.monotonic() - t0) * 1000)

        try:
            resp = self.client.post(url, headers=headers, json=body, timeout=ep.timeout)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:      # its text holds the full request URL: the status alone is recorded
            return CallResult(None, f"HTTP {e.response.status_code}", duration_ms=ms(), sent=True)
        except httpx.HTTPError as e:
            detail = scrub_secrets(str(e), *ep.secrets())[:MAX_REASON_CHARS]
            return CallResult(None, f"{type(e).__name__}: {detail}", duration_ms=ms(), sent=True)
        except (ValueError, TypeError, httpx.InvalidURL) as e:     # the request could not be sent (a bad header, a URL)
            return CallResult(None, f"request not sent: {type(e).__name__}")
        if len(resp.content) > MAX_RESPONSE_BYTES:
            return CallResult(None, "the response is too large to read", duration_ms=ms(), sent=True)
        try:
            data = loads_bounded(resp.text)
        except ValueError:
            return CallResult(None, "the response is not JSON", duration_ms=ms(), sent=True)
        usage = usage_of(ep, data)
        reply = parse_reply(ep, data)
        if reply.text is None:
            return CallResult(None, reply.why or "no answer in the response", usage, ms(), True)
        text = chat_text(reply.text)
        if not text:
            return CallResult(None, "the reply was empty", usage, ms(), True)
        return CallResult(text, "", usage, ms(), True)
