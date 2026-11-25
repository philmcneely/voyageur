"""Browser (gated on Playwright and a chromium build): the testbed's French page, drift-fr, is probed and sent to POST /v1/overlay with no
locators, and every locator the service writes for a text element is run on the live page: it finds exactly one element, and that
element is the one the mark is drawn on. Four sets of what a request can carry (the aria snapshot, the probe's copy counts, both,
neither) say what is written and what is refused, and why. Skipped when Playwright or its browser is not installed."""
import base64
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
PROBE = (ROOT / "src" / "voyageur" / "probe" / "probe.js").read_text(encoding="utf-8")
DRIFT_FR = ROOT / "testbed" / "site" / "drift-fr" / "index.html"
VIEW = {"width": 900, "height": 700}

# the page's twelve listed elements, in the probe's order, and the locator each is written with for a request that carries the aria
# snapshot and the copy counts (the four that no hook names are the headings and the wrapping label)
WITH_BOTH = [
    'page.get_by_role("heading", name="Paramètres du compte", level=1, exact=True)',
    'page.get_by_test_id("profile-panel")',
    'page.get_by_role("heading", name="Profil", level=2, exact=True)',
    "page.locator(\"label[for='display-name']\")",
    'page.get_by_test_id("display-name-input")',
    'page.get_by_text("Recevoir la lettre d\'information", exact=True)',
    "page.locator(\"input[data-pref='newsletter']\")",
    'page.get_by_test_id("save-settings")',
    'page.locator("#danger")',
    'page.get_by_role("heading", name="Zone dangereuse", level=2, exact=True)',
    'page.get_by_test_id("delete-account")',
    'page.get_by_test_id("summary")',
]
NO_HOOK = (0, 2, 5, 9)                                   # the elements no stable attribute names


@pytest.fixture(scope="module")
def captured():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"chromium is not available: {type(e).__name__}")
        try:
            page = browser.new_page(viewport=VIEW)
            page.goto(DRIFT_FR.as_uri())
            data = page.evaluate(PROBE)
            aria = page.locator("body").aria_snapshot()
            png = page.screenshot(type="png")
            yield page, data, aria, png
        finally:
            browser.close()


def overlay(captured, aria=True, counts=True):
    from fastapi.testclient import TestClient
    from voyageur.app import create_app
    from tests.unit.scenarios import JSON, FakeModel, session
    _, data, aria_text, png = captured
    page = {"url": "http://app.example/", "inventory": data["items"], "inventory_more": data["more"],
            "viewport": {"w": VIEW["width"], "h": VIEW["height"], "dpr": 1, "scroll": {"x": 0, "y": 0}},
            "screenshot": {"media_type": "image/png", "data": base64.b64encode(png).decode(), "width": VIEW["width"],
                           "height": VIEW["height"]}}
    if counts:
        page.update(inventory_copy_more=data["copy_more"], inventory_copy_withheld=data["copy_withheld"],
                    inventory_copy_withheld_data=data["copy_withheld_data"])
    if aria:
        page["aria_snapshot"] = aria_text
    c = TestClient(create_app(session(FakeModel()), None), base_url="http://127.0.0.1", headers=JSON)
    r = c.post("/v1/overlay", json={"client": {"framework": "x", "language": "python"}, "page": page, "options": {"labels": "locators"}})
    assert r.status_code == 200, r.text
    return r.json()["legend"]


def run_locator(page, code):
    """The locator `code` (service-written, a plain `page.<locator chain>`) on the live page; the namespace holds only the page."""
    return eval(code, {"__builtins__": {}}, {"page": page})            # noqa: S307 (test only: the code is the service's own output)


def test_the_page_lists_twelve_elements_and_eight_of_them_are_text(captured):
    items = captured[1]["items"]
    assert [i["tag"] for i in items] == ["h1", "section", "h2", "label", "input", "label", "input", "button", "section", "h2", "button", "p"]
    from voyageur.marks import element_kind
    assert sum(1 for i in items if element_kind(i) == "text") == 8


def test_with_the_snapshot_and_the_counts_every_locator_finds_exactly_the_marked_element(captured):
    page, data, _, _ = captured
    legend = overlay(captured)
    assert [e["locator"] for e in legend] == WITH_BOTH
    assert all(e["status"] == "matched" for e in legend)
    for entry, item in zip(legend, data["items"]):
        found = run_locator(page, entry["locator"])
        assert found.count() == 1, entry["locator"]
        live = found.bounding_box()
        assert live is not None and abs(live["x"] - item["box"]["x"]) < 1 and abs(live["y"] - item["box"]["y"]) < 1 \
            and abs(live["width"] - item["box"]["width"]) < 1, (entry["locator"], live, item["box"])
    assert [(e["kind"], e["copy_based"]) for i, e in enumerate(legend) if i in NO_HOOK] == \
        [("role", True), ("role", True), ("text", True), ("role", True)]


def test_without_the_counts_the_snapshot_still_writes_the_headings_and_the_label_says_what_it_lacks(captured):
    page, _, _, _ = captured
    legend = overlay(captured, counts=False)
    assert [e["locator"] for i, e in enumerate(legend) if i != 5] == [c for i, c in enumerate(WITH_BOTH) if i != 5]
    label = legend[5]
    assert label["status"] == "no_stable_locator" and label["locator"] is None and label["reason"] == "copy_counts_missing"
    assert "copy counts" in label["why"]
    for e in legend:
        if e["locator"]:
            assert run_locator(page, e["locator"]).count() == 1, e["locator"]


def test_without_the_snapshot_the_counts_prove_text_and_the_text_is_labelled_copy_based(captured):
    page, _, _, _ = captured
    legend = overlay(captured, aria=False)
    assert [e["locator"] for e in legend][0] == 'page.get_by_text("Paramètres du compte", exact=True)'
    assert [e["locator"] for e in legend][2] == 'page.get_by_text("Profil", exact=True)'
    assert all(e["copy_based"] for i, e in enumerate(legend) if i in NO_HOOK)
    for e in legend:
        assert e["locator"] and run_locator(page, e["locator"]).count() == 1, e["locator"]


def test_with_neither_the_four_are_refused_with_a_reason_and_the_other_eight_are_unchanged(captured):
    legend = overlay(captured, aria=False, counts=False)
    for i, e in enumerate(legend):
        if i in NO_HOOK:
            assert e["status"] == "no_stable_locator" and e["locator"] is None and e["reason"] and e["why"], i
        else:
            assert e["locator"] == WITH_BOTH[i], i
