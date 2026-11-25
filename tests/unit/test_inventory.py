"""Unit: evaluate a locator against the inventory the client sent (does it point at anything?)."""
import pytest

from voyageur.inventory import Inventory
from voyageur.uniqueness import Uniqueness

pytestmark = pytest.mark.unit

TODO = [
    {"tag": "input", "class": "new-todo", "placeholder": "What needs to be done?"},               # NO type attribute
    {"tag": "input", "type": "checkbox", "class": "toggle"},
    {"tag": "button", "id": "clear", "class": "clear-completed", "name": "Clear completed", "hidden": True},
    {"tag": "a", "name": "All", "class": "selected"},
    {"tag": "input", "type": "text", "name_attr": "q", "label": "Search", "testid": "search", "title": "find", "alt": "s"},
]
ARIA = '- textbox "Search"\n- link "All"\n- heading "todos"\n- text: Clear completed'
INV = Inventory(TODO, True, ARIA)


def test_an_incomplete_inventory_never_says_zero():
    # rewritten: the original read a "(+40 more elements not listed)" marker; the client now says `complete` itself
    inv = Inventory([{"tag": "input", "id": "a"}], False)
    assert not inv.complete and inv.count("page.locator('#nope')") is None


def test_no_elements_means_unknown_never_zero():
    for inv in (Inventory([], True, '- heading "Stub page"'), Inventory([], False)):
        assert inv.count("page.locator('#nope')") is None and inv.count("page.getByPlaceholder('x')") is None


def test_entries_that_are_not_plain_dicts_of_strings_are_dropped():
    inv = Inventory(["x", None, {"tag": "input", "id": "a", "placeholder": 5, "class": ["c"], 7: "x"},
                     {"tag": "button", "id": "b", "hidden": "yes"}], True)
    assert [e["tag"] for e in inv.elements] == ["input", "button"]
    assert "placeholder" not in inv.elements[0] and "class" not in inv.elements[0] and "hidden" not in inv.elements[1]
    assert inv.count("page.locator('#a')") == 1 and inv.count("page.getByPlaceholder('5')") == 0


@pytest.mark.parametrize("pattern", ["(a+)+$" + "x" * 300, "[", "(", "*abc", "(?P<n>a)(?P<n>b)"])
def test_a_hostile_or_malformed_regex_in_a_locator_matches_nothing_and_never_raises(pattern):
    inv = Inventory([{"tag": "input", "placeholder": "a" * 40 + "!", "name": "a" * 40}], True)
    assert inv.count("page.getByPlaceholder(/" + pattern + "/)") in (0, None)      # None: the call is not even parsed
    assert inv.matched("page.getByText(/" + pattern + "/)") in ([], None)


def test_a_catastrophic_regex_is_not_run():
    # `(a+)+$` against 28 a's and a "!" took 33 seconds when it was run
    import time
    inv = Inventory([{"tag": "input", "placeholder": "a" * 28 + "!", "name": "a" * 28 + "!"}], True)
    t0 = time.monotonic()
    for expr in ("page.getByPlaceholder(/(a+)+$/)", "page.getByPlaceholder(/(a|aa)+$/)", "page.getByText(/(\\w+\\s?)*$/)",
                 "page.getByPlaceholder(/(a)\\1$/)"):
        assert inv.count(expr) in (0, None)
    assert time.monotonic() - t0 < 1


def test_ordinary_locator_regexes_still_match():
    inv = Inventory([{"tag": "input", "placeholder": "Save draft"}, {"tag": "input", "placeholder": "Pay now"}], True)
    assert inv.count("page.getByPlaceholder(/^save/i)") == 1
    assert inv.count("page.getByPlaceholder(/pay|save/i)") == 2
    assert inv.count("page.getByPlaceholder(/(pay|save) \\w+/i)") == 2          # a group that is not quantified


def test_get_by_text_reads_element_text_when_there_is_no_aria_snapshot():
    inv = Inventory([{"tag": "p", "text": "Order shipped"}, {"tag": "p", "text": "Order shipped", "hidden": True},
                     {"tag": "p", "text": "Other"}], True)
    assert inv.count("page.getByText('Order shipped')") == 1          # hidden text is not matched, as in Playwright
    assert Inventory(inv.elements, False).count("page.getByText('Order shipped')") is None
    assert [e["text"] for e in inv.matched("page.get_by_text('Other')")] == ["Other"]


@pytest.mark.parametrize("expr,n", [
    ("page.locator('input[type=\"text\"]')", 1),            # only the search input has a type attribute of text
    ("page.locator('input')", 3),
    ("page.locator('input.new-todo')", 1),
    ("page.locator('input[type=\"checkbox\"]')", 1),
    ("page.locator('input[placeholder]')", 1),
    ("page.locator('input[placeholder^=\"What\"]')", 1),
    ("page.locator('#clear')", 1),                          # hidden elements still match CSS
    ("page.locator('button.clear-completed')", 1),
    ("page.locator('#nope')", 0),
    ("page.locator('[data-testid=\"search\"]')", 1),
    ("page.locator('[data-testid=\"missing\"]')", 0),
    ("page.locator('input[name=q]')", 1),
    ("page.locator('textarea')", 0),
])
def test_css_compound_selectors(expr, n):
    assert INV.count(expr) == n


def test_the_todomvc_regression_input_type_text_does_not_match_the_new_todo_input():
    """`input[type="text"]` is what the healer guessed; the real input has no type attribute."""
    only_todo = Inventory(TODO[:1], True)
    assert only_todo.count("this.page.locator('input[type=\"text\"]')") == 0
    assert only_todo.count("this.page.getByPlaceholder('What needs to be done?')") == 1
    assert only_todo.count("this.page.locator('input.new-todo')") == 1


@pytest.mark.parametrize("expr", [
    "page.locator('.new-todo')",             # bare class: the listing does not cover every element
    "page.locator('input[foo=bar]')",         # attribute the inventory does not carry
    "page.locator('div.a > b')",              # combinator
    "page.locator('input:visible')",          # pseudo-class
    "page.locator('text=Clear')",             # engine
    "page.locator('a, button')",              # union
    "page.getByRole('button', { name: 'Clear completed' })",   # no aria role list -> aria_count None? checked below
])
def test_unknown_stays_unknown(expr):
    if "getByRole" in expr:
        assert Inventory(TODO, True, "").count(expr) is None
    else:
        assert INV.count(expr) is None


@pytest.mark.parametrize("expr,n", [
    ("page.getByPlaceholder('What needs')", 1), ("page.getByPlaceholder('What needs', { exact: true })", 0),
    ("page.getByPlaceholder('what needs to be done?', { exact: true })", 0), ("page.getByPlaceholder('Nope')", 0),
    ("page.get_by_placeholder('What needs to be done?', exact=True)", 1),
    ("page.getByTestId('search')", 1), ("page.getByTestId('zzz')", 0), ("page.get_by_test_id('search')", 1),
    ("page.getByLabel('Search')", 1), ("page.getByLabel('Nothing')", 0),
    ("page.getByAltText('s', { exact: true })", 1), ("page.getByTitle('find')", 1),
    ("page.getByText('Clear completed')", 1), ("page.getByText('Nowhere')", 0),
])
def test_user_facing_locators(expr, n):
    assert INV.count(expr) == n


def test_role_uses_the_failure_time_aria_tree():
    assert INV.count("page.getByRole('textbox', { name: 'Search' })") == 1
    assert INV.count("page.getByRole('textbox', { name: 'What needs to be done?' })") == 0
    assert INV.count("page.getByRole('link', { name: 'All' })") == 1


def test_chains():
    assert INV.count("page.locator('input').first()") == 1
    assert INV.count("page.locator('input').nth(5)") == 0 and INV.count("page.locator('input').nth(2)") == 1
    assert INV.count("page.locator('#nope').filter({ hasText: 'x' })") == 0           # zero base is certain
    assert INV.count("page.locator('#clear').filter({ hasText: 'x' })") is None       # non-zero base: filter unknown
    assert INV.count("page.locator('#nope').getByRole('button')") == 0
    assert INV.count("page.locator('input')") == 3 and INV.count("page.locator('input').last()") == 1


# --- wired into Uniqueness ---------------------------------------------------------------------
def test_missing_rejects_zero_only():
    u = Uniqueness()
    assert "matches no element in the page snapshot" in u.missing("page.locator('input[type=\"text\"]')", Inventory(TODO[:1], True))
    assert u.missing("page.getByPlaceholder('What needs to be done?')", INV) is None
    assert u.missing("page.locator('.new-todo')", INV) is None                  # unknown is not missing
    assert u.missing("page.locator('#nope')", None) is None
    assert Uniqueness(existence=False).missing("page.locator('#nope')", INV) is None


def test_ambiguity_uses_inventory_counts():
    assert "matches 2 elements in the page snapshot" in Uniqueness().ambiguity("page.locator('input[type]')", "typescript", "/", "", INV)
    assert Uniqueness().ambiguity("page.locator('input.new-todo')", "typescript", "/", "", INV) is None


# ---- elements listed only for their copy ---------------------------------------------------------------------------
# an aria snapshot folds neighbouring plain text into one line, so it cannot count a div's text on its own
FOLDED = "- text: Hi there, is the build green? Sign in with SSO\n- searchbox \"Search\""
CHAT = {"tag": "div", "text": "Hi there, is the build green?", "copy_only": True}
SSO = {"tag": "div", "text": "Sign in with SSO", "copy_only": True}


def test_a_copy_only_text_the_aria_snapshot_folded_into_its_neighbours_is_counted_from_the_listing():
    inv = Inventory([dict(CHAT), dict(SSO), {"tag": "input", "type": "search"}], True, FOLDED)
    assert inv.count("page.get_by_text('Hi there, is the build green?', exact=True)") == 1
    assert [e["text"] for e in inv.matched("page.get_by_text('Sign in with SSO', exact=True)")] == ["Sign in with SSO"]


def test_a_copy_only_text_on_the_page_twice_counts_twice_and_hidden_ones_do_not_count():
    twin = {**CHAT, "box": {"x": 1, "y": 1, "width": 9, "height": 9}}
    inv = Inventory([dict(CHAT), twin], True, "- text: Hi there, is the build green? Hi there, is the build green?")
    assert inv.count("page.get_by_text('Hi there, is the build green?', exact=True)") == 2
    inv = Inventory([dict(CHAT), {**CHAT, "hidden": True}], True, FOLDED)
    assert inv.count("page.get_by_text('Hi there, is the build green?', exact=True)") == 1


def test_text_with_no_copy_only_element_is_counted_from_the_snapshot_alone_as_before():
    inv = Inventory([{"tag": "button", "name": "Clear completed"}, {"tag": "div", "data_x": "1", "text": "Clear completed"}],
                    True, "- button \"Clear completed\"")
    assert inv.count("page.get_by_text('Clear completed', exact=True)") == 1


def test_a_label_is_counted_by_its_for_and_getbytext_never_finds_the_control_it_names():
    inv = Inventory([{"tag": "label", "for_attr": "d", "text": "Birth date"}, {"tag": "input", "id": "d", "label": "Birth date"}],
                    True, "- text: Birth date\n- textbox")
    assert inv.count("page.locator('label[for=\"d\"]')") == 1 and inv.count("page.locator('label[for=\"x\"]')") == 0
    assert [e["tag"] for e in inv.matched("page.get_by_text('Birth date', exact=True)")] == ["label"]
    assert Inventory(inv.elements, False).count("page.locator('label[for=\"x\"]')") is None


def test_a_wrapper_with_the_text_of_the_listed_element_inside_it_is_not_a_second_match():
    # getByText matches the smallest element holding the text: a wrapper the probe says holds only the inner element's copy is not one
    outer = {"tag": "div", "id": "app", "text": "Earn 10 coins", "copy_wrap": True, "box": {"x": 0, "y": 0, "width": 200, "height": 40}}
    inner = {"tag": "div", "text": "Earn 10 coins", "copy_only": True, "box": {"x": 0, "y": 0, "width": 200, "height": 40}}
    aside = {"tag": "div", "text": "Other", "copy_only": True, "box": {"x": 0, "y": 50, "width": 200, "height": 40}}
    inv = Inventory([outer, inner, aside], True, "- text: Earn 10 coins Other")
    assert inv.count("page.get_by_text('Earn 10 coins', exact=True)") == 1
    assert [e.get("copy_only") for e in inv.matched("page.get_by_text('Earn 10 coins', exact=True)")] == [True]
    twin = {**inner, "box": {"x": 0, "y": 100, "width": 200, "height": 40}}
    assert Inventory([outer, inner, twin], True, "- text: Earn 10 coins Earn 10 coins").count(
        "page.get_by_text('Earn 10 coins', exact=True)") == 2


def test_a_text_count_that_reads_the_listing_is_unknown_when_copy_holding_elements_were_left_out():
    els = [dict(CHAT), {"tag": "button", "name": "Go", "text": "Go"}]
    expr = "page.get_by_text('Hi there, is the build green?', exact=True)"
    assert Inventory(els, True, FOLDED, copy_more=3).count(expr) is None                  # with a snapshot
    assert Inventory(els, True, "", copy_more=3).count(expr) is None                      # without one
    assert Inventory(els, True, "", copy_more=3).count("page.get_by_text('Go', exact=True)") is None
    assert Inventory(els, True, "", copy_more=3).matched(expr) is None
    assert Inventory(els, True, FOLDED).count(expr) == 1 and Inventory(els, True, "").count(expr) == 1


def test_an_item_that_says_the_copy_list_was_cut_has_the_same_effect_as_the_count():
    cut = {**CHAT, "copy_cut": True}
    inv = Inventory([cut], True, FOLDED)
    assert inv.copy_more > 0 and inv.count("page.get_by_text('Hi there, is the build green?', exact=True)") is None
    assert Inventory([cut], True, FOLDED, copy_more=0).copy_more > 0


def test_a_cut_copy_list_leaves_the_other_counts_alone():
    inv = Inventory([{**CHAT, "copy_cut": True}, {"tag": "input", "placeholder": "Search", "testid": "q"}], True, FOLDED, copy_more=5)
    assert inv.count("page.get_by_test_id('q')") == 1 and inv.count("page.get_by_placeholder('Search')") == 1
    assert Inventory([{"tag": "button", "name": "Clear completed"}], True, "- button \"Clear completed\"", copy_more=5).count(
        "page.get_by_role('button', name='Clear completed')") == 1


def test_the_scoped_inventories_keep_the_cut_copy_list():
    from voyageur.row_scope import row_scope
    holder = {"tag": "div", "testid": "row-1", "box": {"x": 0, "y": 0, "width": 200, "height": 50}}
    leaf = {"tag": "div", "text": "Hi", "copy_only": True, "row": "row-1", "box": {"x": 1, "y": 1, "width": 20, "height": 10}}
    inv = Inventory([holder, leaf], True, "", copy_more=2)
    sub = row_scope(inv.elements[1], inv, "python", "page")[2]
    assert sub.copy_more == 2


def test_two_texts_one_drawn_inside_the_others_box_are_two_matches_unless_the_probe_says_one_wraps_the_other():
    big = {"tag": "div", "text": "Save", "copy_only": True, "box": {"x": 0, "y": 0, "width": 100, "height": 100}}
    small = {"tag": "div", "text": "Save", "copy_only": True, "box": {"x": 5, "y": 5, "width": 10, "height": 10}}
    inv = Inventory([big, small], True, "- text: Save Save")
    assert inv.count("page.get_by_text('Save', exact=True)") == 2 and len(inv.matched("page.get_by_text('Save', exact=True)")) == 2
    wrapped = Inventory([{**big, "copy_wrap": True}, small], True, "- text: Save Save")
    assert wrapped.count("page.get_by_text('Save', exact=True)") == 1


def test_a_substring_text_count_from_a_listing_with_copy_only_items_is_unknown_since_withheld_copy_could_match():
    inv = Inventory([dict(CHAT)], True, "")
    assert inv.count("page.get_by_text('Hi there')") is None and inv.matched("page.get_by_text('Hi there')") is None
    assert inv.count("page.get_by_text('Hi there, is the build green?', exact=True)") == 1       # exact: withheld copy cannot equal it
    assert Inventory([{"tag": "div", "data_x": "1", "text": "Order shipped"}], True, "").count("page.getByText('Order shipped')") == 1
    with_aria = Inventory([dict(CHAT)], True, FOLDED)
    assert with_aria.count("page.get_by_text('Hi there')") is None


# ---- copy the probe withheld (too long, an address, digits, inside a data widget) ------------------------------------------
SAVE_BUTTON = {"tag": "button", "id": "b", "name": "Save", "text": "Save"}
SAVE_LEAF = {"tag": "div", "text": "Save", "copy_only": True}
ARIA_SAVE = '- button "Save"\n- text: Save your code 123456'


def test_a_substring_text_count_is_unknown_when_the_probe_withheld_visible_copy_and_the_count_comes_from_the_listing():
    listing_only = Inventory([dict(SAVE_BUTTON)], True, "", withheld=1)
    assert listing_only.count("page.get_by_text('Save')") is None                    # a withheld paragraph may contain it
    assert listing_only.count("page.get_by_text('Save', exact=True)") == 1           # but cannot equal it (not a data widget)
    assert Inventory([dict(SAVE_BUTTON)], True, "", withheld=0).count("page.get_by_text('Save')") == 1
    assert listing_only.matched("page.get_by_text('Save')") is None


def test_an_exact_text_count_is_unknown_when_copy_inside_a_data_widget_was_withheld():
    inv = Inventory([dict(SAVE_LEAF), {"tag": "button", "id": "x", "name": "Go"}], True, '- text: Save\n- button "Go"',
                    withheld=1, withheld_data=1)
    assert inv.count("page.get_by_text('Save', exact=True)") is None                 # a grid cell "Save" would be a second match
    assert inv.matched("page.get_by_text('Save', exact=True)") is None
    assert Inventory([dict(SAVE_LEAF)], True, "- text: Save", withheld=1).count("page.get_by_text('Save', exact=True)") == 1


def test_the_snapshot_that_holds_the_withheld_copy_keeps_the_count_when_no_listed_copy_is_involved():
    inv = Inventory([dict(SAVE_BUTTON)], True, ARIA_SAVE, withheld=1, withheld_data=1)
    assert inv.count("page.get_by_text('Save')") == 2                                # both lines are in the snapshot
    assert inv.count("page.get_by_text('Save', exact=True)") == 1


def test_the_scoped_inventories_inherit_what_the_probe_withheld():
    inv = Inventory([dict(SAVE_LEAF)], True, "", copy_more=1, withheld=2, withheld_data=3)
    sub = Inventory([dict(SAVE_LEAF)], True, "")
    sub.inherit(inv)
    assert (sub.copy_more, sub.withheld, sub.withheld_data) == (1, 2, 3)


def test_an_exact_text_that_would_itself_be_withheld_is_unknown_not_zero_when_the_count_reads_the_listing():
    inv = Inventory([dict(SAVE_BUTTON)], True, "")
    for want in ("x" * 81, "mail me@x.org", "code 123456", "eyJhbGciOiJIUzI1NiJ9abc123xyz"):
        assert inv.count(f"page.get_by_text('{want}', exact=True)") is None, want
        assert inv.matched(f"page.get_by_text('{want}', exact=True)") is None, want
    assert inv.count("page.get_by_text('Save', exact=True)") == 1 and inv.count("page.get_by_text('Nope', exact=True)") == 0
    with_aria = Inventory([dict(SAVE_BUTTON)], True, '- button "Save"')
    assert with_aria.count("page.get_by_text('code 123456', exact=True)") == 0       # the snapshot holds all the page's text


def test_a_listing_with_copy_only_items_from_a_client_that_sent_no_withheld_counts_reads_as_not_clean():
    leaf = dict(SAVE_LEAF)
    assert Inventory([leaf], True, "- text: Save", withheld=None, withheld_data=None).withheld_data >= 1
    assert Inventory([leaf], True, "- text: Save", withheld=0, withheld_data=0).withheld_data == 0
    assert Inventory([dict(SAVE_BUTTON)], True, "", withheld=None, withheld_data=None).withheld_data == 0     # no copy_only: an old probe
    assert Inventory([leaf], True, "- text: Save", withheld=None, withheld_data=None).count("page.get_by_text('Save', exact=True)") is None


def test_the_length_a_withheld_text_is_measured_in_is_the_probes_utf16_units():
    inv = Inventory([dict(SAVE_BUTTON)], True, "")
    emoji = "\U0001F600" * 41                              # 41 code points, 82 UTF-16 units: the probe withholds it
    assert inv.count(f"page.get_by_text('{emoji}', exact=True)") is None
    assert inv.count(f"page.get_by_text('{'x' * 80}', exact=True)") == 0
