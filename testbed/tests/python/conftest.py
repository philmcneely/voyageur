"""
===============================================================================
Testbed Fixtures (the page, the language helper and the heal client's two jobs around a test)
===============================================================================
Fixtures for the testbed's Python suites. `page` is a headless chromium page (plain Playwright, so the suites run without
pytest-playwright) whose action and expect() timeouts come from QA_ACTION_TIMEOUT_MS (default 5000). `by_language` is the helper
a healed line may call, `by_language(page, {"en": <locator>, "fr": <locator>})`: the entry for the page's <html lang>, the first
entry for any other language. Real frameworks ship that helper; here it is put in builtins so a patched test file needs no import
line and line numbers never move.

The two switches below stand in for what a framework's heal client does around a test. Both are off in a plain `pytest` run and
are set by run_acceptance.py, which needs the page as it was AT the failure (a menu open, a tab chosen), not a reopened route.

Features:
    ✓ HEAL_TESTBED_RECORD=<dir>: before each action on a locator, count what the locator matches and describe the one element it
      hits with the service's probe (probe.js in `only` mode); at the end of the session write <dir>/statements.json keyed
      "<test file>:<line of the statement>". On a clean base-site run it is the fingerprint record (text and attributes only,
      never a typed value); on a re-run after a patch it is the count and hit check of the patched statement
    ✓ HEAL_TESTBED_FAILURES=<dir>: when a test fails, write <dir>/<test name>.json with the page URL, the probe's listing and
      the aria snapshot of the page as it was at the failure and, when HEAL_TESTBED_TARGETS names a golden list, the probe
      description of the element the golden heal for that statement ends on (what a scripted fake model picks)
    ✓ HEAL_TESTBED_SCREENSHOT=1: the same dump also holds one viewport screenshot (never full page) as `screenshot_b64` and
      the viewport it was taken in (`viewport`: w, h, dpr, scroll), the evidence a heal client sends when vision is on
    ✓ HEAL_PROBE_JS names the file holding the probe (the acceptance run fetches it from the service's /v1/probe.js)

Environment Variables:
    QA_ACTION_TIMEOUT_MS, HEAL_TESTBED_RECORD, HEAL_TESTBED_FAILURES, HEAL_TESTBED_TARGETS, HEAL_TESTBED_SCREENSHOT,
    HEAL_PROBE_JS

Author: PMAC
===============================================================================
"""
import base64
import builtins
import json
import os
import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Locator, expect, sync_playwright

TIMEOUT = int(os.environ.get("QA_ACTION_TIMEOUT_MS", "5000"))
HERE = Path(__file__).resolve().parent
ACTIONS = ("click", "dblclick", "fill", "check", "uncheck", "set_checked", "select_option", "hover", "press", "type",
           "clear", "focus")
RECORDED = {}
ACTION_TAIL = re.compile(r"\.(?:" + "|".join(ACTIONS) + r")\(.*$")


def by_language(page, branches):
    """The locator written for the page's language, else the first one (the language the test was written in)."""
    lang = (page.evaluate("document.documentElement.lang") or "").split("-")[0].lower()
    return branches.get(lang) or next(iter(branches.values()))


builtins.by_language = by_language


def _probe():
    path = os.environ.get("HEAL_PROBE_JS")
    return Path(path).read_text(encoding="utf-8") if path else None


def _describe(locator, probe):
    """The probe's description of the one element a locator hits, or None (no match, several, or not describable)."""
    try:
        found = locator.first.evaluate(probe, timeout=min(TIMEOUT, 2000))
        return found["items"][0] if found and found.get("items") else None
    except Exception:
        return None


def _frame_of(locator):
    """The name the probe gives the frame the located element sits in (its name, else its id, else 'iframe'), or ''. The probe
    describes a lone element without it, and the page can hold twins of that element outside the frame."""
    try:
        return locator.first.evaluate("el => { const f = el.ownerDocument.defaultView.frameElement; "
                                      "return f ? (f.getAttribute('name') || f.id || 'iframe').slice(0, 80) : ''; }",
                                      timeout=min(TIMEOUT, 2000))
    except Exception:
        return ""


def _statement_line():
    """'<file>:<line>' of the test statement running the action: the nearest frame in a test_*.py file next to this one."""
    frame = sys._getframe(2)
    while frame is not None:
        name = Path(frame.f_code.co_filename)
        if name.parent == HERE and re.match(r"test_.*\.py$", name.name):
            return f"{name.name}:{frame.f_lineno}"
        frame = frame.f_back
    return None


def _instrument():
    probe = _probe()
    if not probe:
        return
    for action in ACTIONS:
        original = getattr(Locator, action)

        def wrapped(self, *args, __original=original, **kwargs):
            key = _statement_line()
            if key and key not in RECORDED:
                try:
                    count = self.count()
                except Exception:
                    count = None
                RECORDED[key] = {"count": count, "element": _describe(self, probe) if count == 1 else None,
                                 "lang": ""}
                try:
                    RECORDED[key]["lang"] = self.page.evaluate("document.documentElement.lang") or ""
                except Exception:
                    pass
            return __original(self, *args, **kwargs)
        setattr(Locator, action, wrapped)


if os.environ.get("HEAL_TESTBED_RECORD"):
    _instrument()


@pytest.fixture
def page(request):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        pg = context.new_page()
        expect.set_options(timeout=TIMEOUT)
        request.node.page_for_report = pg
        try:
            yield pg
        finally:
            context.close()
            browser.close()


def _failing_line(call):
    """(line number, text) of the innermost frame of the failure that is in a test file next to this one."""
    for entry in call.excinfo.traceback:
        path = Path(str(entry.path))
        if path.parent == HERE and path.name.startswith("test_"):
            return entry.lineno + 1, path.read_text(encoding="utf-8").split("\n")[entry.lineno]
    return None, ""


def _dump_failure(item, call, pg):
    directory = Path(os.environ["HEAL_TESTBED_FAILURES"])
    directory.mkdir(parents=True, exist_ok=True)
    probe = _probe()
    line, text = _failing_line(call)
    dump = {"url": pg.url, "line": line}
    if probe:
        dump["probe"] = pg.evaluate(probe)
        if os.environ.get("HEAL_TESTBED_SCREENSHOT"):
            dump["viewport"] = pg.evaluate("({w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio, "
                                           "scroll: {x: window.scrollX, y: window.scrollY}})")
            dump["screenshot_b64"] = base64.b64encode(pg.screenshot(type="png")).decode()      # the viewport, as it is now
        try:
            dump["aria"] = pg.locator("body").aria_snapshot()
        except Exception:
            dump["aria"] = None
        targets = os.environ.get("HEAL_TESTBED_TARGETS")
        if targets and text:
            for t in json.loads(Path(targets).read_text(encoding="utf-8")):
                if t["old"] in text and t.get("test") in (None, item.name):
                    dump["target_error"] = None
                    dump["target_item"] = None
                    expr = t["target"]
                    if t.get("compose") and t.get("new"):
                        # the failing statement with the golden replacement in place, minus the action: the element the
                        # whole chain ends on (what a heal of a frame_locator hop has to land on is the element inside)
                        expr = ACTION_TAIL.sub("", text.strip().replace(t["old"], t["new"]))
                    if expr:
                        try:
                            located = eval(expr, {"page": pg})        # trusted expressions from the golden files
                            located = getattr(located, "owner", located) if type(located).__name__ == "FrameLocator" else located
                            dump["target_item"] = _describe(located, probe)
                            dump["target_frame"] = _frame_of(located)
                        except Exception as exc:              # kept: a missing target is a bench gap worth seeing
                            dump["target_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                    break
    (directory / f"{item.name}.json").write_text(json.dumps(dump), encoding="utf-8")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    pg = getattr(item, "page_for_report", None)
    if os.environ.get("HEAL_TESTBED_FAILURES") and report.when == "call" and report.failed and pg is not None:
        _dump_failure(item, call, pg)


def pytest_sessionfinish(session, exitstatus):
    directory = os.environ.get("HEAL_TESTBED_RECORD")
    if directory and RECORDED:
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / "statements.json").write_text(json.dumps(RECORDED, indent=1), encoding="utf-8")
