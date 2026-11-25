"""Browser (gated on Playwright and a chromium build): the overlay's marks land on the elements a real page shows. The probe's boxes
are in document coordinates, a viewport screenshot is taken at the page's scroll and device pixel ratio, and the overlay has to put
each mark where the element really is, at dpr 1 and 2, scrolled or not. Skipped when Playwright or its browser is not installed."""
import io
from pathlib import Path

import pytest
from PIL import Image

from voyageur.overlay import _PALETTE, render

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


def test_unscrolled_the_same_marks_would_be_somewhere_else(browser):
    png, items, view = shot_and_probe(browser, 1, 1000)
    boxes = [(i["box"]["x"], i["box"]["y"], i["box"]["width"], i["box"]["height"]) for i in items]
    wrong = render(png, boxes, (view["w"], view["h"], view["dpr"]), (0, 0))      # the scroll left out: nothing is on screen
    assert wrong.drawn == (1,)                                                   # only the top button, drawn over empty page
