"""Browser (gated on Playwright and a chromium build): what probe.js returns for real pages. Skipped, with the reason
in the report, when Playwright or its browser is not installed."""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

PROBE = (Path(__file__).resolve().parents[2] / "src" / "voyageur" / "probe" / "probe.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:      # no browser build installed
            pytest.skip(f"chromium is not available: {type(e).__name__}")
        try:
            yield b
        finally:
            b.close()


def probe(browser, html, **kw):
    page = browser.new_page(**kw)
    try:
        page.set_content(html)
        return page.evaluate(PROBE)
    finally:
        page.close()


def find(items, **want):
    return [i for i in items if all(i.get(k) == v for k, v in want.items())]


# --- nothing a person typed ------------------------------------------------------------------------------
TYPED = """<!doctype html><html><body>
<input id="plain" type="text">
<input id="pw" type="password" aria-label="Password">
<textarea id="notes"></textarea>
<div id="rich" contenteditable="true">initial rich secret</div>
<select id="plan"><option value="a">alpha</option><option value="b" selected>bravo-chosen</option></select>
<form id="f"><label for="card">Card</label><input id="card" type="text"></form>
<div id="wrap" data-x="1"><textarea>SEEDED-IN-MARKUP</textarea><span>visible copy</span></div>
<input type="button" id="btn" value="Pay now"><input type="submit" id="sub" value="Send it">
</body></html>"""


def test_typed_values_appear_nowhere_in_the_probe_output(browser):
    page = browser.new_page()
    try:
        page.set_content(TYPED)
        page.fill("#plain", "TYPED-SECRET-ONE")
        page.fill("#pw", "TYPED-PASSWORD-TWO")
        page.fill("#notes", "TYPED-NOTES-THREE")
        page.fill("#card", "4111111111111111")
        page.evaluate("document.getElementById('rich').textContent = 'TYPED-RICH-FOUR'")
        out = json.dumps(page.evaluate(PROBE))
    finally:
        page.close()
    for secret in ("TYPED-SECRET-ONE", "TYPED-PASSWORD-TWO", "TYPED-NOTES-THREE", "4111111111111111", "TYPED-RICH-FOUR",
                   "initial rich secret", "SEEDED-IN-MARKUP", "bravo-chosen"):
        assert secret not in out, secret
    assert "visible copy" in out                       # ordinary copy is still read


def test_an_unlabeled_text_input_and_a_password_input_are_listed_without_a_name_or_text(browser):
    page = browser.new_page()
    try:
        page.set_content(TYPED)
        page.fill("#plain", "TYPED-SECRET-ONE")
        page.fill("#pw", "TYPED-PASSWORD-TWO")
        items = page.evaluate(PROBE)["items"]
    finally:
        page.close()
    plain, pw = find(items, id="plain")[0], find(items, id="pw")[0]
    assert plain["name"] == "" and plain["text"] == "" and plain["label"] == "" and plain["box"]
    assert pw["type"] == "password" and pw["text"] == "" and pw["name"] == "Password"      # the aria-label, not the value
    assert find(items, id="notes")[0]["text"] == "" and find(items, id="rich")[0]["text"] == ""
    assert find(items, id="plan")[0]["text"] == ""


def test_a_button_like_input_keeps_its_label_and_a_container_skips_the_controls_inside(browser):
    items = probe(browser, TYPED)["items"]
    assert find(items, id="btn")[0]["name"] == "Pay now" and find(items, id="sub")[0]["text"] == "Send it"
    assert find(items, id="wrap")[0]["text"] == "visible copy"


def test_a_control_character_in_an_attribute_value_comes_out_without_it(browser):
    html = ('<div id="d" data-x="line one&#10;line two&#13;&#0;x" title="a&#9;b" data-y="clean"></div>'
            '<button id="b" aria-label="a&#10;b">go</button>')
    out = probe(browser, html)
    text = json.dumps(out, ensure_ascii=False)
    item = find(out["items"], id="d")[0]
    assert "\n" not in item["data_x"] and "\r" not in item["data_x"] and "\u0000" not in item["data_x"]
    assert item["data_x"].startswith("line one") and "line two" in item["data_x"] and item["title"] == "a b"
    assert item["data_y"] == "clean"
    assert "\\n" not in text and "\\r" not in text and "\\u0000" not in text
    assert find(out["items"], id="b")[0]["aria_label"] == "a b"


def test_data_attributes_named_like_credentials_are_dropped(browser):
    html = ('<div id="d" data-csrf-token="C1" data-auth="A1" data-session-id="S1" data-user-email="a@b.c" '
            'data-secret-key="K1" data-password-hint="P1" data-order="1042" data-qa="row"></div>')
    item = find(probe(browser, html)["items"], id="d")[0]
    assert not [k for k in item if any(w in k for w in ("csrf", "auth", "session", "email", "secret", "password"))]
    assert item["data_order"] == "1042" and item["data_qa"] == "row"
    assert not any(v in json.dumps(item) for v in ("C1", "A1", "S1", "a@b.c", "K1", "P1"))


def test_a_data_attribute_whose_key_could_be_another_names_is_not_listed(browser):
    # data-foo_bar and data-foo-bar would both be data_foo_bar; data-a.b and an over-long name would be refused (422)
    html = ('<div id="d" data-foo_bar="x" data-foo-bar="y" data-a.b="z" data-' + "n" * 61 + '="w" data-ok="v"></div>')
    item = find(probe(browser, html)["items"], id="d")[0]
    assert item["data_foo_bar"] == "y" and item["data_ok"] == "v"
    assert not [k for k, v in item.items() if k.startswith("data_") and v and k not in ("data_foo_bar", "data_ok")]


# --- shape -----------------------------------------------------------------------------------------------
def test_testid_is_data_testid_only_and_the_other_hooks_are_separate_keys(browser):
    html = ('<button id="a" data-testid="t1" data-test="t2" data-qa="t3" data-cy="t4">x</button>'
            '<button id="b" data-test="only-test">y</button>')
    items = probe(browser, html)["items"]
    a, b = find(items, id="a")[0], find(items, id="b")[0]
    assert (a["testid"], a["data_test"], a["data_qa"], a["data_cy"]) == ("t1", "t2", "t3", "t4")
    assert b["testid"] == "" and b["data_test"] == "only-test"
    assert "data_testid" not in a


def test_an_item_has_no_ref_and_carries_text_and_disabled(browser):
    items = probe(browser, '<button id="on">Go</button><button id="off" disabled>Stop</button>'
                           '<a role="button" id="aria" aria-disabled="true">Link</a>')["items"]
    assert all("ref" not in i for i in items)
    assert find(items, id="on")[0]["disabled"] is False and find(items, id="on")[0]["text"] == "Go"
    assert find(items, id="off")[0]["disabled"] is True and find(items, id="aria")[0]["disabled"] is True


def test_the_page_record_has_no_query_or_hash_and_reports_language_viewport_scroll_and_dpr(browser):
    page = browser.new_page(locale="fr-FR", viewport={"width": 800, "height": 600}, device_scale_factor=2)
    try:
        page.goto("data:text/html,<html lang='fr-CA'><body>x</body></html>")
        rec = page.evaluate(PROBE)["page"]
    finally:
        page.close()
    assert set(rec) == {"path", "lang", "browser_lang", "viewport", "scroll", "dpr"}
    assert rec["lang"] == "fr-CA" and rec["browser_lang"].startswith("fr") and rec["dpr"] == 2
    assert rec["viewport"] == {"width": 800, "height": 600} and rec["scroll"] == {"x": 0, "y": 0}


def test_the_path_comes_without_the_query_or_the_hash(browser):
    page = browser.new_page()
    try:
        page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body="<p id='x'>hi</p>"))
        page.goto("http://app.example/orders/42?token=SECRET-IN-QUERY#access=SECRET-IN-HASH")
        data = page.evaluate(PROBE)
    finally:
        page.close()
    assert data["page"]["path"] == "/orders/42"
    assert "SECRET-IN-QUERY" not in json.dumps(data) and "SECRET-IN-HASH" not in json.dumps(data)


def test_at_most_500_items_are_returned_and_more_counts_the_rest(browser):
    out = probe(browser, "".join(f'<button id="b{i}">B{i}</button>' for i in range(620)))
    assert len(out["items"]) == 500 and out["more"] == 120


def test_called_with_an_element_the_function_describes_only_that_element(browser):
    page = browser.new_page()
    try:
        page.set_content('<table><tr data-qa="order-row-1"><td data-col="customer">Acme</td></tr></table><button>x</button>')
        data = page.locator("[data-col=customer]").evaluate(PROBE)
    finally:
        page.close()
    assert data["more"] == 0 and len(data["items"]) == 1
    item = data["items"][0]
    assert item["tag"] == "td" and item["data_col"] == "customer" and item["row"] == "order-row-1" and item["box"]
    assert data["page"]["lang"] == ""


# --- what it reaches (ported from the original browser tests) ------------------------------------------------
PAGE = """<!doctype html><html><body>
<table><tr data-qa="order-row-1" data-activity="1041"><td data-col="customer">Acme</td>
  <td><button data-qa="status" aria-label="Order status">pending</button></td></tr></table>
<div class="chips"><button data-filter="paid">Paid</button></div>
<ul role="tabpanel" data-panel="history"><li data-event="e301">Payment captured</li></ul>
<order-flag id="flag"></order-flag>
<div class="toolbar"><a class="draw-polygon" title="Draw a polygon">Draw</a>
  <div class="tile" onclick="void 0">Tile</div><span class="chip" tabindex="0">Chip</span>
  <span class="skip" tabindex="-1">Skip</span><div class="card"><p>just words</p></div>
  <div class="pick" style="cursor:pointer"><span class="inner">Pick me</span></div>
  <details><summary>More</summary><p>hidden words</p></details></div>
<iframe name="payment" srcdoc="<input name='card' aria-label='Card number'><button id='pay'>Pay</button>"></iframe>
<script>
  const host = document.getElementById('flag');
  const root = host.attachShadow({mode: 'open'});
  root.innerHTML = '<span role="switch" aria-checked="false" aria-label="Flag for review"></span>';
</script></body></html>"""


@pytest.fixture()
def listing(browser):
    page = browser.new_page()
    try:
        page.set_content(PAGE)
        page.wait_for_function("document.querySelector('iframe').contentDocument.querySelector('#pay')")
        return page.evaluate(PROBE)["items"]
    finally:
        page.close()


def test_an_element_inside_an_open_shadow_root_is_listed_with_its_host(listing):
    switch = find(listing, role="switch")
    assert len(switch) == 1 and switch[0]["shadow"] == "order-flag" and switch[0]["aria_label"] == "Flag for review"


def test_an_element_inside_a_same_origin_frame_is_listed_with_the_frame_name(listing):
    card = find(listing, name_attr="card")
    assert len(card) == 1 and card[0]["frame"] == "payment" and card[0]["box"] is not None
    assert find(listing, id="pay")[0]["frame"] == "payment"
    assert len(find(listing, tag="iframe")) == 1


def test_data_attributes_are_listed_on_controls_and_on_plain_elements(listing):
    assert find(listing, data_filter="paid") and find(listing, data_panel="history")
    cell = find(listing, data_col="customer")
    assert len(cell) == 1 and cell[0]["tag"] == "td"
    assert find(listing, data_event="e301")[0]["tag"] == "li"


def test_an_element_names_the_row_it_sits_in_by_the_nearest_ancestor_hook(listing):
    status = find(listing, data_qa="status")[0]
    assert status["row"] == "order-row-1" and status["aria_label"] == "Order status"


def test_things_a_step_can_act_on_are_listed_though_they_are_not_controls(listing):
    names = {(i["tag"], i["name"]) for i in listing}
    assert ("a", "Draw") in names and ("div", "Tile") in names and ("span", "Chip") in names
    assert ("summary", "More") in names and ("div", "Pick me") in names
    assert find(listing, tag="a")[0]["title"] == "Draw a polygon"


def test_the_listing_does_not_become_a_dump_of_every_element(listing):
    names = {(i["tag"], i["name"]) for i in listing}
    assert ("div", "just words") not in names and ("span", "Pick me") not in names      # a wrapper, a listed element's own copy
    assert not find(listing, tag="div", class_="card") and not find(listing, tag="div", class_="toolbar")
    assert not find(listing, tag="p", text="hidden words")                             # inside a closed details: not visible
    assert not [i for i in listing if i["tag"] in ("html", "body", "table", "tbody", "details")]


def test_a_label_inside_a_closed_details_is_still_read(browser):
    html = ('<details><summary>More options</summary><label for="referral">Invite code</label>'
            '<input id="referral" name="referral"><button id="apply">Apply code</button></details>')
    items = {i["id"]: i for i in probe(browser, html)["items"] if i["id"]}
    assert items["referral"]["label"] == "Invite code" and items["apply"]["name"] == "Apply code"


def test_aria_labelledby_names_a_control(browser):
    html = ('<span id="mk">Marketing emails</span>'
            '<button id="sw" type="button" role="switch" aria-checked="false" aria-labelledby="mk"></button>')
    items = {i["id"]: i for i in probe(browser, html)["items"] if i["id"]}
    assert items["sw"]["label"] == "Marketing emails" and items["sw"]["role"] == "switch"


def test_hidden_text_inside_a_button_is_not_part_of_its_name(browser):
    items = probe(browser, '<button id="b">Save <span style="display:none">tooltip</span><b>d</b></button>')["items"]
    assert find(items, id="b")[0]["name"] == "Save d"


def test_a_page_that_clobbers_parentelement_cannot_hang_the_listing(browser):
    data = probe(browser, "<form><input name='parentElement'><button>Go</button></form>")
    assert any(i["name"] == "Go" for i in data["items"])


def test_a_pointer_cursor_on_body_does_not_list_the_whole_page(browser):
    data = probe(browser, "<body style='cursor:pointer'><p>words</p><div id='x'>x</div></body>")
    assert [(i["tag"], bool(i.get("copy_only"))) for i in data["items"]] == [("p", True), ("div", False)]      # not html or body


def test_top_at_center_says_whether_a_click_at_the_box_centre_hits_the_element(browser):
    html = ('<button id="free" style="position:absolute;left:10px;top:10px;width:80px;height:30px">Free</button>'
            '<button id="under" style="position:absolute;left:10px;top:60px;width:80px;height:30px">Under</button>'
            '<div style="position:absolute;left:0;top:50px;width:200px;height:60px;background:#fff"></div>'
            '<button id="far" style="position:absolute;left:10px;top:5000px;width:80px;height:30px">Far</button>'
            '<button id="wrap" style="position:absolute;left:10px;top:120px;width:80px;height:30px"><span>Inner</span></button>')
    items = probe(browser, html)["items"]
    assert find(items, id="free")[0]["top_at_center"] is True
    assert find(items, id="under")[0]["top_at_center"] is False          # a covering div takes the click
    assert "top_at_center" not in find(items, id="far")[0]                # off-screen is unknown, not false
    assert find(items, id="wrap")[0]["top_at_center"] is True             # a child of the element counts as the element


# --- the row an element sits in, and the attribute it came from ---------------------------------------------
ROWS = """<!doctype html><html><body>
<table><tr data-qa="order-row-1" data-testid="ignored"><td><button data-qa="status">paid</button></td></tr>
<tr data-testid="order-row-2"><td><button data-qa="status">paid</button></td></tr></table>
<section id="billing"><button data-cy="save">Save</button></section>
<button data-qa="loose">Loose</button>
</body></html>"""


def test_each_item_says_which_hook_attribute_its_row_came_from(browser):
    items = probe(browser, ROWS)["items"]
    one, two = find(items, data_qa="status")
    assert (one["row"], one["row_attr"]) == ("order-row-1", "data-qa")        # data-qa wins, as in the row value
    assert (two["row"], two["row_attr"]) == ("order-row-2", "data-testid")
    assert (find(items, data_cy="save")[0]["row"], find(items, data_cy="save")[0]["row_attr"]) == ("billing", "id")
    loose = find(items, data_qa="loose")[0]
    assert "row" not in loose and "row_attr" not in loose


# --- pw_text: the text Playwright's has_text reads, so the service can prove a .filter(has_text=...) --------------
PW_CASES = [
    # (the target row's inner html, the other row's, the text, whether a narrowed locator is written): the review's
    # wrong-element shapes (blocks joined with no space live, a hidden duplicate, a button's value, a shadow root) and plain ones
    ("<h3>My</h3><p>subscription</p>", 'Other <span style="display:none">My subscription</span>', "My subscription", False),
    ("<span>My subscription</span>", 'Other <input type="button" value="My subscription">', "My subscription", False),
    ("<span>My subscription</span>", 'Other <span style="display:none">my  SUBSCRIPTION</span>', "My subscription", False),
    ("<span>Alpha</span>", '<span id="host"></span>', "alpha", False),
    ("<span>FooBar</span>", '<input type="reset" value="FooBar">', "FooBar", False),
    ("<span>FooBar</span>", "<svg><style>FooBar</style></svg>", "FooBar", False),
    ('Foo<input type="button" value=" Bar">', "<span>Other</span>", "FooBar", False),     # live it reads "Foo Bar"
    ("<span>Plan B</span>", '<div role="combobox">Plan B</div>', "Plan B", False),
    ("<span>My subscription</span>", "<span>Other</span>", "My subscription", True),
    ("<b>Edit</b>  <i>billing.py</i>", "<b>Edit</b><i>app.py</i>", "billing.py", True),
]


@pytest.mark.parametrize("mine, other, text, written", PW_CASES)
def test_the_text_filter_proof_agrees_with_what_playwright_matches(browser, mine, other, text, written):
    from voyageur.inventory import Inventory
    from voyageur.schemas import InventoryItem
    from voyageur.text_filter import text_filtered
    html = f'<div data-testid="row" id="a">{mine}</div><div data-testid="row" id="b">{other}</div>'
    page = browser.new_page()
    try:
        page.set_content(html)
        page.evaluate("() => { const h = document.getElementById('host'); if (h) h.attachShadow({mode: 'open'})"
                      ".innerHTML = '<b>ALPHA</b>'; }")
        items = page.evaluate(PROBE)["items"]
        flat = [InventoryItem.model_validate(i).flat(n) for n, i in enumerate(items)]
        inv = Inventory(flat, True, "")
        target = next(e for e in inv.elements if e.get("id") == "a")
        out = text_filtered(target, inv, "python", "page", "chain", "data-testid", f"page.get_by_text({json.dumps(text)})")
        live = page.get_by_test_id("row").filter(has_text=text)
        assert (out is not None) == written
        if out is not None:                                    # claimed unique and the target: Playwright must agree
            assert live.count() == 1 and live.get_attribute("id") == "a", out.candidates[0].code["python"]
    finally:
        page.close()


def test_pw_text_never_reads_a_textarea_a_select_or_an_editable_region(browser):
    out = probe(browser, TYPED)
    blob = json.dumps(out)
    assert "SEEDED-IN-MARKUP" not in blob and "bravo-chosen" not in blob and "initial rich secret" not in blob
    wrap = find(out["items"], id="wrap")[0]
    assert wrap["pw_cut"] is True


def test_pw_text_with_a_token_like_run_is_not_sent(browser):
    out = probe(browser, '<div id="t" data-x="1"><span hidden>eyJhbGciOiJIUzI1NiJ9abc123xyz</span>Visible</div>')
    item = find(out["items"], id="t")[0]
    assert item["pw_text"] == "" and item["pw_cut"] is True and "eyJhbG" not in json.dumps(out)


def test_a_text_entry_role_written_as_a_token_list_is_never_read(browser):
    out = probe(browser, '<div id="w" data-x="1"><div role="textbox foo">TYPED-ONE</div></div>'
                         '<div id="v" data-x="2"><div role=" SEARCHBOX">TYPED-TWO</div></div>')
    assert "TYPED-ONE" not in json.dumps(out) and "TYPED-TWO" not in json.dumps(out)
    assert find(out["items"], id="w")[0]["pw_cut"] is True


# --- elements that hold copy, listed for the steps that find an element by its text ---------------------------------
def test_an_element_with_copy_of_its_own_is_listed_as_copy_only_in_page_order(browser):
    html = ('<div><p>Intro line</p><button id="go">Go</button><div class="msg">Hi there, is the build green?</div>'
            '<h2>Pricing</h2></div>')
    items = probe(browser, html)["items"]
    copy = [(i["tag"], i["text"]) for i in items if i.get("copy_only")]
    assert copy == [("p", "Intro line"), ("div", "Hi there, is the build green?"), ("h2", "Pricing")]
    assert [i["tag"] for i in items] == ["p", "button", "div", "h2"]            # between the others, in page order
    assert not find(items, id="go")[0].get("copy_only") and all(i["box"] and not i["hidden"] for i in items)


def test_a_copy_only_item_has_the_facts_a_text_locator_needs(browser):
    items = probe(browser, '<div class="css-view-175oi2r">Sign in with SSO</div>')["items"]
    assert items[0]["copy_only"] is True and items[0]["text"] == "Sign in with SSO" and items[0]["name"] == "Sign in with SSO"
    assert items[0]["pw_text"] == "Sign in with SSO" and items[0]["class"] == "css-view-175oi2r"


def test_copy_that_is_a_listed_elements_own_is_not_listed_twice(browser):
    items = probe(browser, '<button id="b"><span>Save</span></button><a href="#"><b>Home</b> page</a>')["items"]
    assert [i["tag"] for i in items] == ["button", "a", "b"] and [i["tag"] for i in items if i.get("copy_only")] == ["b"]


def test_hidden_copy_and_copy_longer_than_the_cap_are_not_listed(browser):
    html = ('<div style="display:none">gone</div><div style="visibility:hidden">unseen</div><div style="width:0;overflow:hidden">'
            'zero</div><p>' + "word " * 20 + '</p><p>fits</p>')
    assert [i["text"] for i in probe(browser, html)["items"]] == ["fits"]


def test_copy_that_could_be_personal_is_not_listed(browser):
    html = ('<p>jane.doe@example.com</p><p>4111 1111 1111 1111</p><p>eyJhbGciOiJIUzI1NiJ9abc123xyz</p>'
            '<p>Order 12</p><p>Call 555 01</p><p>Code 123456</p>')
    out = probe(browser, html)
    assert [i["text"] for i in out["items"]] == ["Order 12", "Call 555 01"]


def test_copy_inside_a_form_control_an_editable_region_or_a_text_entry_role_is_never_listed(browser):
    out = probe(browser, TYPED + '<div role="textbox"><span>TYPED-ROLE</span></div><datalist><option>OPT-LIST</option></datalist>'
                                 '<p>plain words</p>')
    blob = json.dumps(out)
    for secret in ("TYPED-ROLE", "OPT-LIST", "alpha", "bravo-chosen", "initial rich secret", "SEEDED-IN-MARKUP"):
        assert secret not in blob, secret
    assert any(i.get("copy_only") and i["text"] == "plain words" for i in out["items"])


def test_copy_only_items_fill_the_room_the_others_leave_and_never_push_one_out(browser):
    html = "".join(f"<p>Line {i}</p>" for i in range(300)) + "".join(f'<button id="b{i}">B{i}</button>' for i in range(300))
    out = probe(browser, html)
    assert len(out["items"]) == 500 and out["more"] == 0
    assert len(find(out["items"], tag="button")) == 300                               # every control is there
    assert [i["text"] for i in out["items"] if i.get("copy_only")][:2] == ["Line 0", "Line 1"]
    assert len([i for i in out["items"] if i.get("copy_only")]) == 200
    crowded = probe(browser, "".join(f'<button id="b{i}">B{i}</button>' for i in range(620)) + "<p>Words</p>")
    assert len(crowded["items"]) == 500 and crowded["more"] == 120 and not [i for i in crowded["items"] if i.get("copy_only")]


def test_a_label_with_a_for_is_listed_with_it_even_when_its_copy_is_in_a_child(browser):
    html = ('<label for="birthdate">What is the birth date?</label><input id="birthdate" type="date">'
            '<label for="nick"><span>Nickname</span></label><input id="nick"><label>Wrapping <input id="w"></label>')
    items = probe(browser, html)["items"]
    labels = find(items, tag="label")
    assert [(i["for_attr"], bool(i.get("copy_only"))) for i in labels if i["for_attr"]] == [("birthdate", False), ("nick", False)]
    assert find(items, for_attr="birthdate")[0]["text"] == "What is the birth date?"
    assert any(i.get("copy_only") and i["tag"] == "span" for i in items) is False   # the label already says "Nickname"


def test_called_with_an_element_that_is_listed_only_for_its_copy_it_says_so_as_the_whole_listing_does(browser):
    page = browser.new_page()
    try:
        page.set_content('<div class="m">Hi there</div><button id="go">Go</button>')
        lone = page.locator(".m").evaluate(PROBE)["items"][0]
        whole = find(page.evaluate(PROBE)["items"], text="Hi there")[0]
        button = page.locator("#go").evaluate(PROBE)["items"][0]
    finally:
        page.close()
    assert lone["copy_only"] is True and whole["copy_only"] is True and "copy_only" not in button
    assert {k: v for k, v in lone.items() if k != "box"} == {k: v for k, v in whole.items() if k != "box"}


def test_copy_holding_elements_left_out_for_want_of_room_are_counted_in_copy_more_not_in_more(browser):
    html = "".join(f"<p>Line {i}</p>" for i in range(300)) + "".join(f'<button id="b{i}">B{i}</button>' for i in range(300))
    out = probe(browser, html)
    assert out["more"] == 0 and out["copy_more"] == 100
    assert all(i.get("copy_cut") for i in out["items"] if i.get("copy_only"))          # an item says so too, for a client that
    assert not [i for i in out["items"] if not i.get("copy_only") and i.get("copy_cut")]    # forwards only the list
    crowded = probe(browser, "".join(f'<button id="b{i}">B{i}</button>' for i in range(620)) + "<p>Words</p><p>More</p>")
    assert crowded["more"] == 120 and crowded["copy_more"] == 2


def test_copy_holding_elements_past_the_scan_limit_are_counted_too(browser):
    out = probe(browser, "".join(f"<p>Line {i}</p>" for i in range(600)) + '<button id="b">B</button>')
    assert len(out["items"]) == 500 and out["more"] == 0 and out["copy_more"] == 600 - 499


def test_nothing_left_out_means_copy_more_is_zero_and_no_item_says_otherwise(browser):
    out = probe(browser, "<p>One</p><p>Two</p><button id='b'>B</button>")
    assert out["copy_more"] == 0 and not [i for i in out["items"] if i.get("copy_cut")]
    lone = browser.new_page()
    try:
        lone.set_content("<p class='x'>One</p>")
        assert lone.locator(".x").evaluate(PROBE)["copy_more"] == 0
    finally:
        lone.close()


# --- review fixes: what else of the page the new listing could send ----------------------------------------------------
def test_pw_text_of_a_copy_only_item_never_carries_hidden_copy_the_listing_filters_would_have_withheld(browser):
    html = ('<div class="a">Account<ul style="display:none"><li>a@b.com</li></ul></div>'
            '<div id="x" data-k="1">Mail<span hidden>jane.doe@example.com</span></div>'
            '<div id="y" data-k="2">Card<span hidden>4111 1111 1111 1111</span></div>')
    out = probe(browser, html)
    blob = json.dumps(out)
    assert "a@b.com" not in blob and "jane.doe" not in blob and "4111" not in blob
    for item in (find(out["items"], text="Account")[0], find(out["items"], id="x")[0], find(out["items"], id="y")[0]):
        assert item["pw_text"] == "" and item["pw_cut"] is True


def test_a_short_code_of_six_or_more_digits_is_not_listed_as_copy_but_five_is(browser):
    out = probe(browser, "<p>Code 123456</p><p>Order 12345</p><p>Ref 1234 5678</p>")
    assert [i["text"] for i in out["items"]] == ["Order 12345"] and "123456" not in json.dumps(out)


def test_an_output_is_a_value_and_copy_in_grids_listboxes_and_trees_is_not_listed_as_copy(browser):
    html = ('<output id="o">RESULT-SECRET</output><div role="grid"><div role="row"><span class="c">CellData</span></div></div>'
            '<div role="tree"><span class="t">TreeData</span></div><p>plain words</p>')
    out = probe(browser, html)
    assert "RESULT-SECRET" not in json.dumps(out)                    # the output is listed (it has an id), its value is not
    assert find(out["items"], id="o")[0]["pw_text"] == ""
    assert [i["text"] for i in out["items"] if i.get("copy_only")] == ["plain words"]      # no leaf of a grid, list or tree
    assert find(out["items"], role="grid")                              # the widgets themselves are listed as before


def test_a_role_option_is_still_listed_as_a_control_with_its_name(browser):
    out = probe(browser, '<div role="listbox"><div role="option" aria-label="Choose Friday" tabindex="-1">1</div></div>')
    option = find(out["items"], role="option")[0]
    assert option["name"] == "Choose Friday" and option["text"] == "1" and not option.get("copy_only")


def test_a_wrapper_with_the_copy_of_a_listed_element_inside_it_says_so_by_the_dom_not_by_boxes(browser):
    html = ('<div id="app"><div><div class="leaf">Earn 10 coins</div></div></div>'
            '<div class="a" style="height:100px;width:100px">Save</div>'
            '<div class="b" style="position:relative;top:-90px;height:10px;width:10px">Save</div>')
    items = probe(browser, html)["items"]
    assert find(items, id="app")[0]["copy_wrap"] is True
    assert not [i for i in find(items, text="Save") if i.get("copy_wrap")] and len(find(items, text="Save")) == 2
    assert not find(items, text="Earn 10 coins", copy_only=True)[0].get("copy_wrap")


def test_copy_is_read_within_a_node_budget(browser):
    html = '<div id="d" data-x="1">Early' + "<b></b>" * 3000 + "<span>Late</span></div>"
    item = find(probe(browser, html)["items"], id="d")[0]
    assert item["text"] == "Early"


# --- the third review: withheld copy is counted, hidden inner items mark nothing, listed copy is filtered ---------------
def test_visible_copy_the_filters_kept_out_is_counted_by_cause(browser):
    html = ('<button id="b">Save</button><p>Save your code 123456</p>'
            '<p>' + "long " * 20 + '</p><p>a@b.com</p><p>fine words</p>')
    out = probe(browser, html)
    assert out["copy_withheld"] == 3 and out["copy_withheld_data"] == 0 and out["copy_more"] == 0
    grid = probe(browser, '<div role="grid"><span>Delete</span></div><div role="tree"><i>Node</i></div><p>fine words</p>')
    assert grid["copy_withheld"] == 2 and grid["copy_withheld_data"] == 2
    form = probe(browser, '<select><option>Yes</option></select><textarea>t</textarea><div contenteditable><b>Typed</b></div>')
    assert form["copy_withheld_data"] >= 1 and "Typed" not in json.dumps(form)


def test_copy_that_is_hidden_or_said_by_the_listed_element_around_it_is_not_counted_as_withheld(browser):
    html = ('<p style="display:none">hidden 123456</p><button id="b"><span>Save</span></button><div role="grid" hidden><b>X</b></div>'
            '<p>fine words</p>')
    out = probe(browser, html)
    assert out["copy_withheld"] == 0 and out["copy_withheld_data"] == 0


def test_an_item_described_on_its_own_carries_no_withheld_counts(browser):
    page = browser.new_page()
    try:
        page.set_content("<p class='x'>One</p><p>code 123456</p>")
        lone = page.locator(".x").evaluate(PROBE)
    finally:
        page.close()
    assert lone["copy_withheld"] == 0 and lone["copy_withheld_data"] == 0


def test_a_hidden_inner_item_does_not_make_its_wrapper_a_copy_wrap(browser):
    items = probe(browser, '<div id="a"><span id="s" style="visibility:hidden">Save</span></div><div class="v">Save</div>')["items"]
    assert not find(items, id="a")[0].get("copy_wrap") and find(items, id="s")[0]["hidden"] is True


def test_the_wrapper_flag_is_the_same_whole_page_or_described_alone(browser):
    page = browser.new_page()
    try:
        page.set_content('<div id="app"><div><div class="leaf">Earn 10 coins</div></div></div><div id="b"><b>Other</b> words</div>')
        whole = {i["id"]: i for i in page.evaluate(PROBE)["items"] if i["id"]}
        alone = {k: page.locator("#" + k).evaluate(PROBE)["items"][0] for k in ("app", "b")}
    finally:
        page.close()
    assert whole["app"]["copy_wrap"] is True and alone["app"]["copy_wrap"] is True
    assert "copy_wrap" not in whole["b"] and "copy_wrap" not in alone["b"]


def test_text_name_and_label_of_a_listed_element_are_withheld_when_they_hold_an_address_or_digits(browser):
    html = ('<button id="acct">jane@x.com</button><button id="card" aria-label="Card 4111 1111 1111 1111">Pay</button>'
            '<button id="ar">\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669</button>'
            '<button id="ok">Order 12345</button><button id="tok">eyJhbGciOiJIUzI1NiJ9abc123xyz</button>'
            '<label for="e">me@x.org</label><input id="e">')
    out = probe(browser, html)
    blob = json.dumps(out, ensure_ascii=True)
    for secret in ("jane@x.com", "4111", "me@x.org", "eyJhbG", "\\u0660"):
        assert secret not in blob, secret
    acct, card, ar, ok = (find(out["items"], id=k)[0] for k in ("acct", "card", "ar", "ok"))
    assert acct["text"] == "" and acct["name"] == "" and card["aria_label"] == "" and ar["text"] == ""
    assert ok["text"] == "Order 12345" and ok["name"] == "Order 12345"
    assert find(out["items"], id="e")[0]["label"] == ""


def test_copy_inside_a_listbox_or_a_grid_is_data_not_listed_but_counted(browser):
    # a typeahead's options are somebody's names and addresses as often as page copy: no filter tells them apart
    html = ('<div role="listbox"><div><span class="l">Alice Smith, 12 Oak Street</span></div></div>'
            '<div role="grid"><div><span class="c">Record 77</span></div></div>')
    out = probe(browser, html)
    assert [i for i in out["items"] if i.get("copy_only")] == []       # (the listbox itself is listed, as a role widget is)
    assert out["copy_withheld"] == 2 and out["copy_withheld_data"] == 2


def test_zero_box_copy_that_is_not_display_none_is_counted_as_data(browser):
    # display:contents copy is short page copy an exact text can equal, so it is counted where an exact count looks
    html = ('<select><option>One</option><optgroup label="g"><option>Two</option></optgroup></select>'
            '<div style="display:contents">Contents words</div>'
            '<div style="display:none"><p>hidden</p></div><details><summary>s</summary><p>closed</p></details>')
    out = probe(browser, html)
    assert out["copy_withheld_data"] == 3 and out["copy_withheld"] == 3 and "One" not in json.dumps(out)


def test_nested_display_contents_copy_is_counted(browser):
    out = probe(browser, '<div style="display:contents"><span style="display:contents">Delete</span></div>')
    assert out["copy_withheld_data"] == 1 and out["copy_withheld"] == 1
