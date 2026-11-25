"""Unit: element identity helpers (boxes, same element by stable attributes, fail-closed baseline check, proof)."""
import pytest

from voyageur import elements as E
from voyageur.elements import (could_be_baseline_control, describe_control, identity_match, item_at, norm_text,
                                   proof_of, same_inventory_element, same_place)

pytestmark = pytest.mark.unit


def box(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


@pytest.mark.parametrize("raw,want", [
    (box(1, 2, 3, 4), (1.0, 2.0, 3.0, 4.0)), (box("1", "2", "3", "4"), (1.0, 2.0, 3.0, 4.0)),
    (None, None), ({}, None), (box(-1, 0, 5, 5), None), (box(0, 0, 0, 5), None), (box(0, 0, 5, -1), None),
    (box(float("nan"), 0, 5, 5), None), (box(0, 0, float("inf"), 5), None), (box("a", 0, 5, 5), None),
    ([1, 2, 3, 4], None),
])
def test_box_of(raw, want):
    assert E.box_of(raw) == want


def test_norm_text_collapses_trims_and_casefolds():
    assert norm_text("  Sports \n Club ") == "sports club" and norm_text(None) == "" and norm_text("STRASSE") == "strasse"


def test_item_at_finds_the_element_with_the_recorded_box_and_prefers_the_smallest():
    items = [{"tag": "div", "box": box(10, 10, 100, 40)}, {"tag": "button", "box": box(10.5, 10, 99.5, 40)},
             {"tag": "a", "box": box(300, 10, 50, 20)}, {"tag": "span"}]
    assert item_at(items, (10.0, 10.0, 100.0, 40.0))["tag"] == "button"
    assert item_at(items, (500.0, 500.0, 5.0, 5.0)) is None


def test_same_place_by_overlap_or_by_holding_the_others_centre():
    assert same_place((0, 0, 100, 40), (5, 0, 100, 40))
    assert not same_place((0, 0, 100, 40), (0, 200, 100, 40))
    assert same_place((0, 0, 400, 400), (190, 190, 20, 20))                 # a container holds the small one's centre


def test_same_inventory_element_by_a_stable_attribute_or_by_everything_but_the_layout():
    a = {"tag": "button", "id": "save", "name": "Save", "box": box(1, 1, 5, 5)}
    assert same_inventory_element(a, {"tag": "button", "id": "save", "name": "Enregistrer"})
    assert not same_inventory_element(a, {"tag": "a", "id": "save"})
    assert same_inventory_element({"tag": "p", "text": "x", "hidden": False}, {"tag": "p", "text": "x", "hidden": True})
    assert not same_inventory_element({"tag": "p", "text": "x"}, {"tag": "p", "text": "y"})


def test_identity_match_needs_the_row_when_a_hook_is_shared():
    base = {"tag": "button", "data_cy": "row-menu", "row": "order-row-1043"}
    items = [base, {"tag": "button", "data_cy": "row-menu", "row": "order-row-1042"}]
    assert identity_match({"tag": "button", "data_cy": "row-menu", "row": "order-row-1043"}, base, items)
    assert not identity_match({"tag": "button", "data_cy": "row-menu", "row": "order-row-1042"}, base, items)
    # no row on the hit: the value is on two elements, so it identifies nothing
    assert not identity_match({"tag": "button", "data_cy": "row-menu"}, base, items)
    assert identity_match({"tag": "button", "data_cy": "row-menu"}, base, [base])


def test_a_custom_data_attribute_identifies_an_element_too():
    base = {"tag": "li", "data_panel": "history", "text": "Payment"}
    assert identity_match({"tag": "li", "data_panel": "history", "text": "Paiement"}, base, [base])
    assert not identity_match({"tag": "li", "data_panel": "other", "text": "Payment"}, base, [base])


def test_could_be_baseline_control_fails_closed():
    a = {"tag": "button", "role": "", "type": "button", "label": "", "name": "", "testid": "save-btn", "id": ":r1:"}
    b = {"tag": "button", "role": "", "type": "button", "label": "", "name": "", "testid": "save-btn", "id": ":r9:"}
    assert could_be_baseline_control(a, b)                                  # new React id, same test id
    assert could_be_baseline_control({"tag": "input", "type": "checkbox", "label": "Sports "},
                                     {"tag": "input", "type": "checkbox", "label": "sports"})
    assert could_be_baseline_control({"tag": "button"}, {"tag": "button", "label": "Anything"})   # nothing identifies it
    assert not could_be_baseline_control({"tag": "button", "role": "switch", "label": "Marketing emails"},
                                         {"tag": "input", "type": "checkbox", "label": "Marketing emails"})


def test_a_typed_value_does_not_make_an_input_look_new():
    base = {"tag": "input", "type": "text", "name": "", "name_attr": "promo"}
    now = {"tag": "input", "type": "text", "name": "SAVE20", "name_attr": "promo"}
    assert could_be_baseline_control(now, base)


@pytest.mark.parametrize("method,control,ok", [
    ("select_option", {"tag": "select"}, True), ("select_option", {"tag": "input", "type": "text"}, False),
    ("check", {"tag": "input", "type": "checkbox"}, True), ("check", {"tag": "button", "role": "switch"}, True),
    ("check", {"tag": "input", "type": "text"}, False), ("fill", {"tag": "textarea"}, True),
    ("fill", {"tag": "input", "type": "email"}, True), ("fill", {"tag": "input", "type": "checkbox"}, False),
    ("fill", {"tag": "div", "role": "textbox"}, True), ("set_input_files", {"tag": "input", "type": "file"}, True),
    ("set_input_files", {"tag": "input", "type": "text"}, False), ("click", {"tag": "div"}, True),
])
def test_compatible(method, control, ok):
    assert E.compatible(method, control) is ok


def test_is_interactive():
    assert E.is_interactive({"tag": "BUTTON"}) and E.is_interactive({"tag": "div", "role": "tab"})
    assert not E.is_interactive({"tag": "div"})


def test_describe_control_is_one_bounded_line():
    line = describe_control({"tag": "input", "type": "email", "id": "e", "label": "x" * 500, "disabled": "true",
                             "box": box(10.4, 20.6, 100, 30)})
    assert line.startswith("input type=email id=\"e\"") and "disabled" in line and "box=(10,21,100,30)" in line
    assert len(line) < 200
    assert describe_control({}) == "?"


@pytest.mark.parametrize("then,now,proof", [
    ({"testid": "a"}, {"testid": "b"}, "identity"),
    ({"data_qa": "status", "row": "r1043"}, {"data_qa": "status", "row": "r1042"}, "row"),
])
def test_proof_of_a_different_element(then, now, proof):
    assert proof_of(then, now, [], None)[0] == proof


def test_proof_of_the_identity_now_on_another_element_and_no_proof_otherwise():
    holder = {"testid": "a", "box": box(300, 400, 80, 30)}
    assert proof_of({"testid": "a"}, {"name": "late"}, [holder], box(50, 200, 80, 30))[0] == "moved"
    assert proof_of({"testid": "a"}, {"name": "late"}, [holder, dict(holder, box=box(1, 1, 5, 5))], None) is None
    assert proof_of({"name": "paid"}, {"name": "late"}, [], None) is None                  # copy proves nothing
    assert proof_of({"testid": "a"}, {"testid": "a", "name": "late"}, [], None) is None
    why = proof_of({"testid": "x" * 500}, {"testid": "y"}, [], None)[1]
    assert len(why) < 300


def test_the_original_private_names_still_resolve():
    assert E._same_place is same_place and E._compatible is E.compatible and E._KIND_KEYS == ("tag", "role", "type")
