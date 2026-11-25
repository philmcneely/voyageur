"""Browser (gated on Playwright and a chromium build): the dashboard against a populated service. The token goes in a header and
never a URL, every tile lists the heals behind its number, hostile record text stays text, the page makes no request outside
its own server, and nothing scrolls sideways at phone width in either theme."""
import re

import pytest

from tests.browser.dash_sample import HOSTILE, TOKEN, build_store, make_app, serve

pytestmark = pytest.mark.browser


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


@pytest.fixture(scope="module")
def service():
    store = build_store()
    gen = serve(make_app(store))
    base, _ = next(gen)
    try:
        yield base, store
    finally:
        gen.close()


def open_page(browser, base, hash_="#/", token=True, **ctx):
    context = browser.new_context(**ctx)
    if token:
        context.add_init_script(f"sessionStorage.setItem('heal-token', '{TOKEN}')")
    page = context.new_page()
    problems = []
    page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.goto(base + "/dashboard" + hash_)
    return context, page, problems


def total(page):
    page.wait_for_selector("h2:has-text('Heals (')")
    return int(re.search(r"Heals \(([\d,]+)\)", page.inner_text("h2:has-text('Heals (')"), re.I).group(1).replace(",", ""))


def settle(page, want):
    """The list's total once it reads `want` (the page re-renders after a click), else the last total seen."""
    got = None
    for _ in range(40):
        got = total(page)
        if got == want:
            break
        page.wait_for_timeout(100)
    return got


def test_the_token_form_sends_a_header_and_never_puts_the_token_in_a_url(browser, service):
    base, _ = service
    context, page, problems = open_page(browser, base, token=False)
    seen = []
    page.on("request", lambda r: seen.append((r.url, r.headers.get("authorization"))))
    page.wait_for_selector("input[name=token]")
    page.fill("input[name=token]", "wrong-token")
    page.click("text=Connect")
    page.wait_for_selector("text=That token was not accepted")
    page.fill("input[name=token]", TOKEN)
    page.click("text=Connect")
    page.wait_for_selector(".tiles")
    assert TOKEN not in page.url and all(TOKEN not in url for url, _ in seen)
    assert any(auth == f"Bearer {TOKEN}" for url, auth in seen if "/v1/metrics" in url)
    assert page.evaluate("sessionStorage.getItem('heal-token')") == TOKEN
    assert page.evaluate("localStorage.getItem('heal-token')") is None and TOKEN not in page.evaluate("document.cookie")
    assert not [p for p in problems if "401" not in p]
    context.close()


def test_every_headline_tile_opens_the_list_of_heals_behind_its_number(browser, service):
    base, store = service
    context, page, problems = open_page(browser, base)
    page.wait_for_selector(".tile")
    count = page.locator(".tile").count()
    assert count == 12
    expected = {"Failures seen": store.count_heals(mode="heal"), "Locator drift": store.count_heals(mode="heal", drift=1),
                "Success rate": store.count_heals(mode="heal", outcome="healed"),
                "Fail rate": store.count_heals(mode="heal", outcome="needs_human"),
                "Escaped wrong heals": store.count_heals(mode="heal", pr_state="edited,reverted"),
                "Kept rate": store.count_heals(mode="heal", pr_state="merged_unchanged")}
    for label, want in expected.items():
        page.goto(base + "/dashboard#/")
        page.wait_for_selector(".tile")
        page.locator(".tile").filter(has=page.get_by_text(label, exact=True)).click()
        assert settle(page, want) == want, label
    assert not problems
    context.close()


def test_a_chip_removes_a_filter_and_a_row_opens_the_record(browser, service):
    base, store = service
    context, page, _ = open_page(browser, base, "#/heals?outcome=healed&tier_movement=up")
    assert settle(page, store.count_heals(outcome="healed", tier_movement="up")) == store.count_heals(outcome="healed", tier_movement="up")
    page.click("button[aria-label='remove filter tier_movement']")
    assert settle(page, store.count_heals(outcome="healed")) == store.count_heals(outcome="healed")
    page.locator("tbody tr").first.click()
    page.wait_for_selector("text=Candidates tried")
    assert "#/heal/h_" in page.url and page.locator("h2:has-text('Model calls')").count() == 1
    context.close()


def test_hostile_text_in_a_record_stays_text_in_the_list_the_record_and_the_overview(browser, service):
    base, store = service
    context, page, problems = open_page(browser, base, "#/heals?outcome=needs_human&reason=target_missing&run=" + store.get_heal("h_hostile0000000001")["run_id"])
    page.wait_for_selector("tbody tr")
    assert HOSTILE in page.inner_text("tbody")
    page.locator("tbody tr").first.click()
    page.wait_for_selector("text=Candidates tried")
    assert HOSTILE in page.inner_text("#app")
    page.goto(base + "/dashboard#/")
    page.wait_for_selector(".tile")
    page.wait_for_timeout(300)
    for url in ("#/", "#/heals?run=" + store.get_heal("h_hostile0000000001")["run_id"]):
        page.goto(base + "/dashboard" + url)
        page.wait_for_selector(".tile, tbody")
        page.wait_for_timeout(200)
        assert page.evaluate("window.__pwned") is None
        assert page.locator("#app img, #app script, #app iframe").count() == 0
    assert not problems
    context.close()


def test_the_page_makes_no_request_outside_its_own_server(browser, service):
    base, _ = service
    context, page, problems = open_page(browser, base)
    outside = []
    page.on("request", lambda r: outside.append(r.url) if not r.url.startswith((base, "data:", "blob:")) else None)
    for hash_ in ("#/", "#/heals", "#/heals?outcome=healed"):
        page.goto(base + "/dashboard" + hash_)
        page.wait_for_selector(".tile, tbody")
    assert outside == [] and not problems
    context.close()


def test_the_csv_export_downloads_the_filtered_list_with_formulas_defused(browser, service):
    base, store = service
    store.update_heal("h_hostile0000000001", test_id="=HYPERLINK(\"http://x\",\"y\")")
    context, page, _ = open_page(browser, base, "#/heals?outcome=needs_human&reason=target_missing")
    total(page)
    with page.expect_download() as info:
        page.click("text=Export CSV")
    text = open(info.value.path(), encoding="utf-8").read()
    lines = text.strip().splitlines()
    assert lines[0].startswith("heal_id,created_at") and len(lines) >= 2
    assert "'=HYPERLINK" in text and ',=HYPERLINK' not in text
    assert len(lines) - 1 >= store.count_heals(outcome="needs_human", reason="target_missing") - 5     # quoted newlines may add lines
    context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("hash_", ["#/", "#/heals?outcome=healed", "#/heals?outcome=needs_human"])
def test_nothing_scrolls_sideways_at_phone_width_in_either_theme(browser, service, scheme, hash_):
    base, store = service
    context, page, problems = open_page(browser, base, hash_, viewport={"width": 390, "height": 844}, color_scheme=scheme)
    page.wait_for_selector(".tile, tbody")
    page.wait_for_timeout(200)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert (bg != "rgba(0, 0, 0, 0)") and page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches") == (scheme == "dark")
    page.goto(base + "/dashboard#/heal/" + store.list_heals(limit=1)[0]["heal_id"])
    page.wait_for_selector("text=Candidates tried")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert not problems
    context.close()


def test_a_server_without_a_token_needs_none(browser):
    gen = serve(make_app(build_store(hostile=False), token=None))
    base, _ = next(gen)
    try:
        context, page, problems = open_page(browser, base, token=False)
        page.wait_for_selector(".tiles")
        assert page.locator("input[name=token]").count() == 0 and not problems
        context.close()
    finally:
        gen.close()


def test_the_durability_tile_says_why_it_has_no_percentage_when_retention_is_short_and_shows_one_when_it_is_not(browser):
    for days, shows_number in ((14, False), (45, True)):
        gen = serve(make_app(build_store(hostile=False), retention_days=days))
        base, _ = next(gen)
        try:
            context, page, problems = open_page(browser, base)
            page.wait_for_selector(".tile")
            tile = page.locator(".tile").filter(has=page.get_by_text("Durability", exact=True))
            text = tile.inner_text()
            if shows_number:
                assert "%" in text and "HEAL_RETENTION_DAYS" not in text
            else:
                assert "%" not in text and "HEAL_RETENTION_DAYS" in text and "31" in text
            assert not problems
            context.close()
        finally:
            gen.close()
