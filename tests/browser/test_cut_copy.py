"""Browser (gated on Playwright and a chromium build): a page whose listed elements hold copy longer than the probe's 80-character cut (a
heading, a paragraph, a button named by a long aria-label) and one whose heading is exactly 80 characters is probed, and every locator the
service writes for any of its elements is run in Chromium: it finds exactly one element. The listing is sent whole, as an older probe
would send it (no `text_cut` / `name_cut`), with the flags, with and without the aria snapshot and the copy counts. A head the probe cut
is never written as an exact text or name; the heading that is exactly 80 is written only when the probe, or the snapshot, says it is
whole. Skipped when Playwright or its browser is not installed."""
import pytest

from voyageur.inventory import Inventory
from voyageur.locator_policy import write

from tests.browser.test_probe import PROBE
from tests.browser.test_text_locators import overlay, run_locator

pytestmark = pytest.mark.browser

LONG_HEAD = "Quarterly planning overview for every team across the whole regional organisation in the coming year"
LONG_PARA = ("The committee met on Tuesday to review the budget, the hiring plan and the roadmap, and agreed to meet again next "
             "month to settle what was left open.")
LONG_NAME = "Open the full report that lists every account, every region and every team, with last year's totals as well"
EXACT_80 = "Everything the team shipped in the second half of the year, in one page for you."
HTML = f"""<!doctype html><html lang="en"><body><main>
<h2 tabindex="0">{LONG_HEAD}</h2>
<p tabindex="0">{LONG_PARA}</p>
<h2 tabindex="0">{EXACT_80}</h2>
<button aria-label="{LONG_NAME}">Report</button>
<h2 tabindex="0">A short heading</h2>
</main></body></html>"""
VIEW = {"width": 900, "height": 700}
assert len(LONG_HEAD) > 80 and len(LONG_PARA) > 80 and len(LONG_NAME) > 80 and len(EXACT_80) == 80


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
            page.set_content(HTML)
            data = page.evaluate(PROBE)
            yield page, data, page.locator("body").aria_snapshot(), page.screenshot(type="png")
        finally:
            browser.close()


def listing(captured, flags=True, aria=True, counts=True):
    """An Inventory built from the probe's own output, as an older probe would send it when `flags` is False."""
    _, data, aria_text, _ = captured
    items = [{k: v for k, v in i.items() if flags or k not in ("text_cut", "name_cut")} for i in data["items"]]
    return Inventory(items, data["more"] == 0, aria_text if aria else "", data["copy_more"],
                     data["copy_withheld"] if counts else None, data["copy_withheld_data"] if counts else None)


def test_the_probe_lists_the_long_copy_cut_at_eighty_and_says_so(captured):
    heads = [i for i in captured[1]["items"] if i["tag"] == "h2"]
    assert [len(i["text"]) for i in heads] == [80, 80, 15]
    assert [i.get("text_cut") for i in heads] == [True, False, None]
    para = next(i for i in captured[1]["items"] if i["tag"] == "p")
    button = next(i for i in captured[1]["items"] if i["tag"] == "button")
    assert para["text_cut"] is True and len(para["text"]) == 80
    assert button["name_cut"] is True and len(button["name"]) == 80 and "text_cut" not in button


def test_a_copy_the_probe_stopped_reading_at_its_own_bounds_says_it_was_cut_though_it_is_short(captured):
    other = captured[0].context.browser.new_page()
    try:
        other.set_content('<p tabindex="0">Start' + "<i></i>" * 1100 + " and the end</p>")
        para = next(i for i in other.evaluate(PROBE)["items"] if i["tag"] == "p")
    finally:
        other.close()
    assert para["text"] == "Start"                                    # the read stops at 1000 nodes, with " and the end" unread
    assert para["text_cut"] is True and para["name_cut"] is True


@pytest.mark.parametrize("flags", [True, False], ids=["flags", "older-probe"])
@pytest.mark.parametrize("aria", [True, False], ids=["snapshot", "no-snapshot"])
@pytest.mark.parametrize("counts", [True, False], ids=["counts", "no-counts"])
def test_every_locator_written_for_any_listed_element_finds_exactly_one_element(captured, flags, aria, counts):
    page = captured[0]
    inventory = listing(captured, flags, aria, counts)
    written = 0
    for element in inventory.elements:
        for cand in write(element, inventory, "python", "page", "text").candidates:
            written += 1
            assert run_locator(page, cand.code["python"]).count() == 1, cand.code["python"]
    assert written >= 2                                         # the short heading and the button named "Report" are still written


@pytest.mark.parametrize("flags", [True, False], ids=["flags", "older-probe"])
def test_a_head_the_probe_cut_is_never_written_as_an_exact_text_or_name(captured, flags):
    heads = [i[k] for i in captured[1]["items"] for k in ("text", "name") if i.get(k + "_cut")]
    assert len(heads) == 5                                       # the heading and the paragraph (text, name), the button's name
    for aria in (True, False):
        inventory = listing(captured, flags, aria)
        for element in inventory.elements:
            for cand in write(element, inventory, "python", "page", "text").candidates:
                assert not any(h in cand.code["python"] for h in heads), cand.code["python"]


def test_the_eighty_character_heading_is_written_only_when_the_probe_or_the_snapshot_says_it_is_whole(captured):
    want = f'page.get_by_text("{EXACT_80}", exact=True)'
    page = captured[0]

    def codes(**kw):
        inventory = listing(captured, **kw)
        element = next(e for e in inventory.elements if e.get("text") == EXACT_80)
        return [c.code["python"] for c in write(element, inventory, "python", "page", "text").candidates]
    assert want in codes(aria=False)                                      # the probe says text_cut false
    assert want not in codes(aria=False, flags=False)                     # an older probe: it may be a head
    assert want in codes(aria=True, flags=False)                          # the snapshot holds the whole line
    assert run_locator(page, want).count() == 1


def test_the_overlay_legend_names_no_locator_that_misses(captured):
    page = captured[0]
    for aria in (True, False):
        for e in overlay(captured, aria=aria):
            if e["locator"]:
                assert run_locator(page, e["locator"]).count() == 1, e["locator"]
            else:
                assert e["status"] == "no_stable_locator" and e["reason"], e
