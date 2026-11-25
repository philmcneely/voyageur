"""Shared builders for the core and API tests: a login page that drifted, a fake model client, a session."""
import copy

from voyageur.config import ServiceSettings
from voyageur.core import HealSession
from voyageur.llm import Endpoint, Usage
from voyageur.store import Store
from voyageur.transport import CallResult

JSON = {"Content-Type": "application/json"}           # what a client sends with every POST

PAGE_PY = """class LoginPage:
    def __init__(self, page):
        self.page = page

    def login(self, user, pw):
        self.page.locator('#username').fill(user)
        self.page.locator('#password').fill(pw)
"""

ERROR = "Timeout 30000ms exceeded.\nCall log:\n  - waiting for locator('#username')\n"

USER = {"ref": "e0", "tag": "input", "type": "text", "role": "textbox", "name": "Username", "label": "Username",
        "attrs": {"id": "user", "data-testid": "login-user", "name": "user"}, "box": {"x": 10, "y": 10, "w": 200, "h": 30}}
PASSWORD = {"ref": "e1", "tag": "input", "type": "password", "label": "Password",
            "attrs": {"id": "pwd", "name": "pwd"}, "box": {"x": 10, "y": 50, "w": 200, "h": 30}}
BUTTON = {"ref": "e2", "tag": "button", "name": "Sign in", "text": "Sign in", "attrs": {"id": "go"},
          "box": {"x": 10, "y": 90, "w": 80, "h": 30}}


def request(inventory=None, **over):
    req = {"client": {"framework": "playwright-ai-framework", "language": "python", "version": "1"},
           "test": {"id": "tests/test_login.py::test_login", "title": "login"},
           "failure": {"error": ERROR, "stack": [{"file": "pages/login_page.py", "line": 6}]},
           "source": {"roots": ["pages", "tests"], "files": [{"path": "pages/login_page.py", "content": PAGE_PY}]},
           "page": {"url": "http://app.example/login", "language": {"html_lang": "en", "browser_locale": "en-US"},
                    "inventory": copy.deepcopy(inventory if inventory is not None else [USER, PASSWORD, BUTTON]),
                    "aria_snapshot": None},
           "options": {"max_rounds": 3}, "run": {"id": "run1", "repo": "app"}}
    req.update(over)
    return req


class FakeModel:
    """A model client that answers from a list and counts calls; the usage it reports is fixed."""

    def __init__(self, *replies, usage=(100, 8)):
        self.replies, self.calls, self.usage = list(replies), [], usage

    def call(self, ep, prompt, images=(), heal_id=""):
        self.calls.append({"prompt": prompt, "heal_id": heal_id})
        text = self.replies.pop(0) if self.replies else None
        return CallResult(text, "" if text else "no answer", Usage(*self.usage), 7, True)


ENDPOINT = Endpoint(base_url="http://localhost:11434/v1", model="test-model")


def session(model=None, retention=14, budget=None, **settings):
    s = ServiceSettings(retention_days=retention, run_token_budget=budget, **settings)
    return HealSession(s, Store(":memory:", retention), model if model is not None else FakeModel(), ENDPOINT)
