"""GET /dashboard: one static page, inline script and style only, a CSP that allows nothing else, no markup built from text."""
import base64
import hashlib
import re

import pytest
from fastapi.testclient import TestClient

from voyageur.app import create_app
from voyageur.dashboard import page
from tests.unit.scenarios import JSON, FakeModel, session

pytestmark = pytest.mark.unit


def client(token="tok"):
    return TestClient(create_app(session(FakeModel()), token), base_url="http://127.0.0.1", headers=JSON)


def test_the_page_is_served_without_a_token_with_a_strict_policy():
    r = client().get("/dashboard")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "https:" not in csp and "http:" not in csp
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_the_policy_hashes_match_the_inline_script_and_style():
    html, headers = page()
    csp = headers["Content-Security-Policy"]
    for tag in ("script", "style"):
        for body in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.S):
            digest = "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"
            assert digest in csp


def test_the_page_calls_only_its_own_api_and_loads_nothing_from_anywhere_else():
    html, _ = page()
    assert not re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", html)
    assert not re.search(r"https?://", html)                       # no CDN, no font host, no remote call
    assert "@import" not in html and "url(" not in html and "<link" not in html and "<img" not in html
    assert "<script src" not in html and "<iframe" not in html
    fetches = re.findall(r"""api\(\s*["'`]([^"'`]+)""", html)
    assert fetches and all(f.startswith(("/v1/", "/healthz")) for f in fetches)


def test_text_from_records_is_never_turned_into_markup():
    html, _ = page()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute(\"style\"",
                      "srcdoc", "DOMParser", "createContextualFragment", "javascript:"):
        assert forbidden not in html, forbidden


def test_the_token_is_kept_in_session_storage_and_sent_only_as_a_header():
    html, _ = page()
    assert "sessionStorage" in html and "Authorization" in html and "Bearer " in html
    assert "localStorage.setItem(\"heal-token\"" not in html
    assert not re.search(r"""token=|[?&]token|document\.cookie|\+\s*state\.token\s*\+\s*["']@""", html)
    assert "credentials: \"omit\"" in html and "referrerPolicy: \"no-referrer\"" in html


def test_it_works_with_light_and_dark_and_at_phone_width():
    html, _ = page()
    assert "prefers-color-scheme: dark" in html and 'data-theme="dark"' in html and "max-width: 640px" in html
    assert 'name="viewport"' in html and "prefers-reduced-motion" in html


def test_the_dashboard_route_is_not_listed_as_an_api_and_data_still_needs_the_token():
    c = client()
    assert c.get("/v1/metrics").status_code == 401 and c.get("/v1/heals").status_code == 401
    assert c.get("/v1/metrics", headers={"Authorization": "Bearer tok"}).status_code == 200
