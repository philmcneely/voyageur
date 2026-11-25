"""Browser (gated on Playwright and a chromium build): the overlay's marks land on the elements a real page shows. The probe's boxes
are in document coordinates, a viewport screenshot is taken at the page's scroll and device pixel ratio, and the overlay has to put
each mark where the element really is, at dpr 1 and 2, scrolled or not. Skipped when Playwright or its browser is not installed."""
import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from voyageur.overlay import _PALETTE, Mark, render, render_marks

pytestmark = pytest.mark.browser

PROBE = (Path(__file__).resolve().parents[2] / "src" / "voyageur" / "probe" / "probe.js").read_text(encoding="utf-8")

# four buttons at the corners of a 400 x 300 viewport that is scrolled 1000 px down, one still at the top of the document
PAGE = """<!doctype html><html><body style="margin:0;background:#ffffff">
<button id="top" style="position:absolute;left:10px;top:10px;width:60px;height:30px;border:0;background:#123456">top</button>
<button id="a" style="position:absolute;left:10px;top:1010px;width:60px;height:30px;border:0;background:#ff8800">a</button>
<button id="b" style="position:absolute;left:330px;top:1010px;width:60px;height:30px;border:0;background:#00aa55">b</button>
<button id="c" style="position:absolute;left:10px;top:1260px;width:60px;height:30px;border:0;background:#aa00aa">c</button>
<button id="d" style="position:absolute;left:330px;top:1260px;width:60px;height:30px;border:0;background:#00aaaa">d</button>
<div style="height:3000px"></div></body></html>"""
FILL = {"a": (255, 136, 0), "b": (0, 170, 85), "c": (170, 0, 170), "d": (0, 170, 170)}


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"chromium is not available: {type(e).__name__}")
        try:
            yield b
        finally:
            b.close()


def shot_and_probe(browser, dpr, scroll_y):
    page = browser.new_page(viewport={"width": 400, "height": 300}, device_scale_factor=dpr)
    try:
        page.set_content(PAGE)
        page.evaluate(f"window.scrollTo(0, {scroll_y})")
        data = page.evaluate(PROBE)
        view = page.evaluate("({w: innerWidth, h: innerHeight, dpr: devicePixelRatio, sx: scrollX, sy: scrollY})")
        return page.screenshot(type="png"), data["items"], view
    finally:
        page.close()


@pytest.mark.parametrize("dpr", [1, 2])
def test_marks_land_on_the_elements_of_a_scrolled_page(browser, dpr):
    png, items, view = shot_and_probe(browser, dpr, 1000)
    assert view["sy"] == 1000 and view["dpr"] == dpr
    boxes = [(i["box"]["x"], i["box"]["y"], i["box"]["width"], i["box"]["height"]) for i in items]
    ids = [i["id"] for i in items]
    out = render(png, boxes, (view["w"], view["h"], view["dpr"]), (view["sx"], view["sy"]))
    assert (out.width, out.height) == (400 * dpr, 300 * dpr)          # the four corners make the crop the whole picture
    assert ids[0] == "top" and 1 in out.not_visible                    # the first listed button is above the viewport
    assert out.drawn == (2, 3, 4, 5)
    image = Image.open(io.BytesIO(out.png)).convert("RGB")
    for number, name in zip(out.drawn, ("a", "b", "c", "d")):
        x, y, w, h = boxes[ids.index(name)]
        left, top = round((x - view["sx"]) * dpr), round((y - view["sy"]) * dpr)
        mid_y = top + round(h * dpr / 2)
        assert image.getpixel((left, mid_y)) == _PALETTE[(number - 1) % len(_PALETTE)], (name, number)   # the outline
        inner = (left + round(w * dpr * 0.85), mid_y)                    # clear of the button's own letter
        assert image.getpixel(inner) == FILL[name], (name, "the page's own pixels show inside the mark")


def overlay_request(png, items, view, **over):
    return {"client": {"framework": "x", "language": "python"},
            "page": {"url": "http://app.example/", "inventory": items,
                     "viewport": {"w": view["w"], "h": view["h"], "dpr": view["dpr"], "scroll": {"x": view["sx"], "y": view["sy"]}},
                     "screenshot": {"media_type": "image/png", "data": base64.b64encode(png).decode(),
                                    "width": view["w"] * view["dpr"], "height": view["h"] * view["dpr"]}}, **over}


@pytest.mark.parametrize("dpr", [1, 2])
def test_the_overlay_endpoint_marks_the_locators_a_real_scrolled_page_holds(browser, dpr):
    from fastapi.testclient import TestClient
    from voyageur.app import create_app
    from tests.unit.scenarios import JSON, FakeModel, session
    png, items, view = shot_and_probe(browser, dpr, 1000)
    c = TestClient(create_app(session(FakeModel()), None), base_url="http://127.0.0.1", headers=JSON)
    locators = ["page.locator('#a')", "page.locator('#top')", "page.locator('#b')", "page.locator('#c')", "page.locator('#d')"]
    r = c.post("/v1/overlay", json=overlay_request(png, items, view, locators=locators))
    assert r.status_code == 200, r.text
    out = r.json()
    assert [e["status"] for e in out["legend"]] == ["matched", "not_visible", "matched", "matched", "matched"]
    image = Image.open(io.BytesIO(base64.b64decode(out["image"]["data"]))).convert("RGB")
    assert image.size == (400 * dpr, 300 * dpr)                        # the whole viewport, no crop
    boxes = {i["id"]: i["box"] for i in items}
    for number, name in ((1, "a"), (3, "b"), (4, "c"), (5, "d")):
        b = boxes[name]
        left, top = round((b["x"] - view["sx"]) * dpr), round((b["y"] - view["sy"]) * dpr)
        mid_y = top + round(b["height"] * dpr / 2)
        assert image.getpixel((left, mid_y)) == _PALETTE[(number - 1) % len(_PALETTE)], (name, number)
        assert image.getpixel((left + round(b["width"] * dpr * 0.85), mid_y)) == FILL[name], (name, "the page shows inside the mark")
    every = c.post("/v1/overlay", json=overlay_request(png, items, view, options={"labels": "locators"})).json()
    assert [e["ref"] for e in every["legend"]] == [f"e{n}" for n, i in enumerate(items) if i["id"] != "top"]       # the service numbers entries the probe left no ref on
    assert {e["label"] for e in every["legend"]} == {'page.locator("#a")', 'page.locator("#b")', 'page.locator("#c")', 'page.locator("#d")'}
    assert every["summary"]["not_visible"] == 1                         # the button above the viewport


TEXT_PAGE = """<!doctype html><html lang="en"><body style="margin:0;background:#ffffff;font:14px sans-serif">
<section role="region" aria-label="Account" style="position:absolute;left:10px;top:10px;width:380px;height:280px;border:1px solid #888">
<h1 style="position:absolute;left:10px;top:8px;margin:0;font-size:20px">Welcome back</h1>
<label for="u" style="position:absolute;left:10px;top:50px">Username</label>
<input id="u" data-testid="user" style="position:absolute;left:10px;top:70px;width:200px;height:24px">
<p role="status" style="position:absolute;left:10px;top:110px;margin:0">Your session has expired</p>
<a href="#x" style="position:absolute;left:10px;top:140px">Forgot password?</a>
<button id="go" style="position:absolute;left:10px;top:180px;width:100px;height:30px">Sign in</button>
</section></body></html>"""


def test_the_overlay_endpoint_marks_every_element_a_test_could_assert_on_in_a_real_page(browser):
    from fastapi.testclient import TestClient
    from voyageur.app import create_app
    from tests.unit.scenarios import JSON, FakeModel, session
    page = browser.new_page(viewport={"width": 400, "height": 300}, device_scale_factor=1)
    try:
        page.set_content(TEXT_PAGE)
        data = page.evaluate(PROBE)
        aria = page.locator("body").aria_snapshot()
        png = page.screenshot(type="png")
    finally:
        page.close()
    req = {"client": {"framework": "x", "language": "python"},
           "page": {"url": "http://app.example/", "inventory": data["items"], "inventory_more": data["more"],
                    "inventory_copy_more": data["copy_more"], "inventory_copy_withheld": data["copy_withheld"],
                    "inventory_copy_withheld_data": data["copy_withheld_data"], "aria_snapshot": aria,
                    "viewport": {"w": 400, "h": 300, "dpr": 1, "scroll": {"x": 0, "y": 0}},
                    "screenshot": {"media_type": "image/png", "data": base64.b64encode(png).decode(), "width": 400, "height": 300}},
           "options": {"labels": "locators"}}
    c = TestClient(create_app(session(FakeModel()), None), base_url="http://127.0.0.1", headers=JSON)
    r = c.post("/v1/overlay", json=req)
    assert r.status_code == 200, r.text
    out = r.json()
    kinds = {e["element_kind"] for e in out["legend"]}
    assert kinds == {"interactive", "text"}, [(e["element_kind"], e["locator"]) for e in out["legend"]]
    texts = [e["locator"] for e in out["legend"] if e["element_kind"] == "text"]
    assert any("Welcome back" in (t or "") for t in texts) and any("session has expired" in (t or "") for t in texts)
    assert any("Account" in (t or "") for t in texts), texts                      # the named section
    assert any((e["locator"] or "").startswith("page.get_by_test_id") for e in out["legend"])
    assert all(e["status"] == "matched" for e in out["legend"] if e["element_kind"] == "text" and e["locator"])
    assert out["summary"]["marked"] == len(out["legend"]) >= 6
    only = c.post("/v1/overlay", json={**req, "options": {"kinds": ["text"]}}).json()
    assert {e["element_kind"] for e in only["legend"]} == {"text"}
    image = Image.open(io.BytesIO(base64.b64decode(out["image"]["data"]))).convert("RGB")
    section = next(e for e in out["legend"] if "Account" in (e["locator"] or ""))
    field = next(e for e in out["legend"] if (e["locator"] or "").startswith("page.get_by_test_id"))
    assert section["color"] != field["color"]                                      # a section and the input inside it
    assert image.getpixel((11, 150)) == tuple(int(section["color"][i:i + 2], 16) for i in (1, 3, 5))      # the section's left edge


def test_unscrolled_the_same_marks_would_be_somewhere_else(browser):
    png, items, view = shot_and_probe(browser, 1, 1000)
    boxes = [(i["box"]["x"], i["box"]["y"], i["box"]["width"], i["box"]["height"]) for i in items]
    wrong = render(png, boxes, (view["w"], view["h"], view["dpr"]), (0, 0))      # the scroll left out: nothing is on screen
    assert wrong.drawn == (1,)                                                   # only the top button, drawn over empty page


DRIFT_FR = Path(__file__).resolve().parents[2] / "testbed" / "site" / "drift-fr" / "index.html"


@pytest.mark.parametrize("labels", ["numbers", "locators"])
def test_on_a_real_page_no_tab_covers_another_marked_element_or_tab(browser, labels):
    """Every element the probe lists on the drift-fr testbed page is marked; a tab sits outside its own box, covers no marked box that
    does not hold its element and no other tab, and a line of locator text covers no marked box at all."""
    page = browser.new_page(viewport={"width": 900, "height": 640}, device_scale_factor=1)
    try:
        page.goto(DRIFT_FR.as_uri())
        items = page.evaluate(PROBE)["items"]
        png = page.screenshot(type="png")
    finally:
        page.close()
    boxes = [(i["box"]["x"], i["box"]["y"], i["box"]["width"], i["box"]["height"]) for i in items]
    texts = [f'page.get_by_test_id("{i.get("testid") or "element"}-{n}")' for n, i in enumerate(items, start=1)]
    marks = [Mark(n, b, t if labels == "locators" else "") for n, (b, t) in enumerate(zip(boxes, texts), start=1)]
    out = render_marks(png, marks, (900, 640, 1), labels=labels)
    assert len(out.tabs) == len(marks) >= 10
    assert all(0 <= t[0] < t[2] <= 900 and 0 <= t[1] < t[3] <= 640 for t in out.tabs)
    assert all(not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]) for n, a in enumerate(out.tabs) for b in out.tabs[n + 1:])
    drawn_as_text = {number - 1 for number, _ in out.texts}
    rects = [(x, y, x + w, y + h) for x, y, w, h in boxes]
    for tab, owner in zip(out.tabs, out.tab_owners):
        for j, r in enumerate(rects):
            holds = j != owner and all(a <= b for a, b in zip(r[:2], rects[owner][:2])) and all(a >= b for a, b in zip(r[2:], rects[owner][2:]))
            covers = tab[0] < r[2] and r[0] < tab[2] and tab[1] < r[3] and r[1] < tab[3]
            assert not (covers and (not holds or owner in drawn_as_text)), (owner, j, tab, r)
    assert (labels == "locators") == bool(drawn_as_text)                               # some lines of text fit beside their boxes on this page
