"""
===============================================================================
Dashboard (GET /dashboard: one page, no external service, nothing fetched from anywhere else)
===============================================================================
The built-in view of the heal record: headline tiles, detail panels and the heal list behind every number, reading the JSON
API (/v1/metrics, /v1/heals, /v1/heals/{id}) with the bearer token the person types in. The page is a single static file,
dashboard/dashboard.html, with its script and style inline, so the service can run with no network at all: no CDN, no web
font, no analytics, no image.

    served without a token   a browser cannot send an Authorization header when it navigates to a URL, and the page holds no
                             data and no secret. Every call it makes to the API carries the token and nothing is cached
    token                    typed into the page and kept in sessionStorage (this tab only, gone when it closes); never put in
                             a URL, a cookie or a request line. With no HEAL_SERVICE_TOKEN configured the API is open and
                             the page needs none
    text from records        locators, test ids, reasons and page paths are page copy and source text: the page inserts them
                             with textContent only, never as markup

Features:
    ✓ Content-Security-Policy that allows this page's own inline script and style by hash and nothing else (connect-src
      'self', no images but data:, no frames, no forms, no base tag), plus nosniff, no-referrer and no-store
    ✓ The hashes are computed from the file, so editing the page cannot leave the policy stale
    ✓ page(): (html, headers), read once and cached

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import base64
import hashlib
import re
from functools import lru_cache
from importlib import resources
from typing import Dict, Tuple

_INLINE = re.compile(r"<(script|style)(?:\s[^>]*)?>(.*?)</\1>", re.S)


def _hash(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"


@lru_cache(maxsize=1)
def page() -> Tuple[str, Dict[str, str]]:
    """The dashboard HTML and the response headers that go with it."""
    html = (resources.files("voyageur") / "dashboard" / "dashboard.html").read_text(encoding="utf-8")
    scripts = [_hash(body) for tag, body in _INLINE.findall(html) if tag == "script"]
    styles = [_hash(body) for tag, body in _INLINE.findall(html) if tag == "style"]
    csp = "; ".join(["default-src 'none'", "script-src " + " ".join(scripts), "style-src " + " ".join(styles),
                     "connect-src 'self'", "img-src data:", "base-uri 'none'", "form-action 'none'",
                     "frame-ancestors 'none'"])
    return html, {"Content-Security-Policy": csp, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                  "Cache-Control": "no-store", "X-Frame-Options": "DENY"}
