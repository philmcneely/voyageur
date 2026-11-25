"""The inventory keeps data-* attributes, aria-labels, rows and frames, and answers locators on them."""
import pytest

from voyageur.inventory import Inventory

pytestmark = pytest.mark.unit

ITEMS = [
    {"tag": "button", "name": "Paid", "data_filter": "paid", "row": "toolbar"},
    {"tag": "li", "data_panel": "history", "data_event": "e301", "name": "Payment captured"},
    {"tag": "button", "name": "Open menu for order 1043", "aria_label": "Open menu for order 1043", "role": ""},
    {"tag": "input", "name_attr": "card", "aria_label": "Card number", "name": "Card number", "frame": "payment"},
    {"tag": "span", "role": "switch", "aria_label": "Flag for review", "shadow": "order-flag"},
]


def read_back():
    return Inventory(ITEMS, True)


def test_data_attributes_can_be_selected():
    inv = read_back()
    assert inv.count("page.locator('[data-filter=paid]')") == 1
    assert inv.count("page.locator('[data-panel=history]')") == 1
    assert inv.count("page.locator('li[data-event=\"e301\"]')") == 1
    assert inv.count("page.locator('[data-filter=nothing]')") == 0


def test_get_by_label_reads_the_aria_label_of_any_element():
    inv = read_back()
    assert inv.count("page.get_by_label('Open menu for order 1043')") == 1


def test_a_page_locator_does_not_enter_a_frame():
    inv = read_back()
    assert inv.count("page.get_by_label('Card number')") == 0
    assert read_back().in_frames and read_back().in_frames[0]["frame"] == "payment"


def test_data_attributes_are_selected_as_html_spells_them():
    items = [{"tag": "button", "data_qa": "status", "data_filter": "paid", "testid": "x", "row": "order-row-1"}]
    inv = Inventory(items, True)
    assert inv.elements[0]["data_qa"] == "status" and inv.elements[0]["data_filter"] == "paid"
    assert inv.count("page.locator('[data-filter=paid]')") == 1 and inv.count("page.locator('[data-qa=status]')") == 1


def test_a_getbytestid_that_matches_an_id_gets_the_locator_that_would():
    from voyageur.uniqueness import Uniqueness
    inv = Inventory([{"tag": "div", "id": "panel-history"}, {"tag": "input", "name_attr": "zip"}], True)
    why = Uniqueness().missing("page.get_by_test_id('panel-history')", inv)
    assert why and "id='panel-history'" in why and "locator('#panel-history')" in why
    why2 = Uniqueness().missing("page.getByTestId('zip')", inv)
    assert why2 and "name='zip'" in why2
    assert "No element has the test id" not in Uniqueness().missing("page.get_by_test_id('nothing')", inv)


def test_nth_first_and_last_narrow_the_matched_set():
    items = [{"tag": "button", "name": "Paid", "box": {"x": 1, "y": 1, "width": 5, "height": 5}},
             {"tag": "button", "name": "paid", "row": "r1042", "box": {"x": 1, "y": 2, "width": 5, "height": 5}},
             {"tag": "button", "name": "paid", "row": "r1043", "hidden": True}]
    inv = Inventory(items, True)
    every = inv.matched("page.get_by_role('button', name='paid')")
    assert [h.get("row") for h in every] == [None, "r1042", "r1043"]
    assert [h.get("row") for h in inv.matched("page.get_by_role('button', name='paid').nth(1)")] == ["r1042"]
    assert [h.get("row") for h in inv.matched("page.get_by_role('button', name='paid').first")] == [None]
    assert [h.get("row") for h in inv.matched("page.getByRole('button', { name: 'paid' }).last()")] == ["r1042"]
    assert inv.matched("page.get_by_role('button', name='paid').nth(7)") == []
