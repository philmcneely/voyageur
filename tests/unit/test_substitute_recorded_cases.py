"""Unit: wrong elements a substitute gate once passed, rebuilt offline from recorded runs of a real-locator testbed and
adapted to this service's inputs (inventory dicts plus last_pass): a removed button taken for a renamed neighbour (a51),
and an element that moved into a frame while a page-level twin stands in for it (d42). The locator spellings of the
originals (model-written expressions) do not apply here: the service writes the locator from the chosen element, so
these cases test the element that was chosen. t53 (the subject tag of a chain) is a self-check on written locators."""
import pytest

from voyageur.gates import refusal_for, verify_pick
from voyageur.inventory import Inventory
from voyageur.locator_policy import write

pytestmark = pytest.mark.unit


def box(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def item(ref, tag, bx=None, **kw):
    out = {"ref": ref, "tag": tag}
    out.update({k: v for k, v in kw.items() if v not in ("", False, None)})
    if bx:
        out["box"] = bx
    return out


def fp_of(e):
    return {k: v for k, v in e.items() if k != "ref"}


def verdict(chosen, now, last, neighbours=(), framed=()):
    elements = Inventory(now, True).elements
    framed = Inventory(list(now) + list(framed), True).in_frames
    el = next(e for e in elements + framed if e["ref"] == chosen["ref"])
    return verify_pick(el, {e["ref"] for e in elements}, elements, fp_of(last), None, [fp_of(n) for n in neighbours], framed)


# ---- a51: the button is removed; its renamed neighbour is not its replacement ------------------------------------
SETTINGS = item("b0", "button", box(23, 285.77, 30.44, 29.59), name="Settings", aria_label="Settings")
NOTIFY = item("b1", "button", box(53.44, 285.77, 40, 29.59), name="Notification settings", aria_label="Notification settings")
ACTIONS = item("b2", "button", box(23, 234.17, 67.92, 29.59), name="Actions")
EDIT = item("b3", "button", box(616.94, 345.2, 46.13, 29.59), name="Edit", data_edit="1", row="row-pop")
ALERTS = item("b4", "button", box(23, 285.77, 40, 29.59), name="Alerts", aria_label="Alerts", top_at_center=True)   # moved into the freed place


def test_a_removed_button_is_not_healed_onto_the_renamed_neighbour_that_left_no_other_trace():
    why = verdict(ALERTS, [ACTIONS, ALERTS, EDIT], SETTINGS, [ACTIONS, NOTIFY, EDIT])
    assert why == "renamed_neighbour" and "Notification settings" in why.why and "Settings" in why.why
    assert "removed or replaced" in why.why


def test_a_renamed_target_is_still_healed_when_the_other_twin_is_still_on_the_page():
    notify_now = dict(NOTIFY, ref="b5")
    assert verdict(ALERTS, [ALERTS, notify_now], SETTINGS, [NOTIFY]) is None


def test_a_renamed_target_is_still_healed_when_nothing_else_looks_like_it():
    assert verdict(ALERTS, [ACTIONS, ALERTS, EDIT], SETTINGS, [ACTIONS, EDIT]) is None


def test_the_size_has_to_point_at_the_neighbour_alone():
    same = item("b4", "button", box(23, 285.77, 30.44, 29.59), name="Alerts", aria_label="Alerts", top_at_center=True)
    assert verdict(same, [same], SETTINGS, [NOTIFY]) is None          # as wide as the step's own element too
    neither = item("b4", "button", box(23, 285.77, 90, 29.59), name="Alerts", aria_label="Alerts", top_at_center=True)
    assert verdict(neither, [neither], SETTINGS, [NOTIFY]) is None


def test_a_neighbour_with_another_stable_attribute_is_not_a_twin():
    other = item("b1", "button", box(53.44, 285.77, 40, 29.59), name="Notification settings", testid="notify")
    assert verdict(ALERTS, [ALERTS], SETTINGS, [other]) is None


def test_no_fingerprint_or_no_neighbours_proves_nothing():
    """The two recorded-case rules refuse nothing on a missing fact (a pick still needs the identity proof of proof.py)."""
    el = Inventory([ALERTS], True).elements[0]
    assert refusal_for(el, [el], None) is None
    assert refusal_for(el, [el], fp_of(SETTINGS)) is None
    assert verify_pick(el, {"b4"}, [el], None, None) == "no_identity_proof"


# ---- d42: the step's element moved into a frame; a page-level twin is not it -------------------------------------
def cart(ref, x, y, **kw):
    return item(ref, "button", box(x, y, 46.92, 29.59), type="button", name="Add", data_qa="add-to-shopping-cart", **kw)


FORM2 = cart("c1", 23, 176.98)                                          # the step's own element (the second form's)
FORM1_NOW, FORM3_NOW = cart("c0", 23, 147.39), cart("c2", 23, 312.58)   # form 3 pushed down by form 2's move
IN_FRAME = cart("c3", 38, 187.98, frame="iframe")


def test_a_page_level_twin_cannot_be_an_element_that_moved_into_a_frame():
    why = verdict(FORM3_NOW, [FORM1_NOW, FORM3_NOW, IN_FRAME], FORM2)
    assert why == "moved_into_frame" and "frame" in why.why and "Locate the frame first" in why.why


def test_the_page_level_pick_is_fine_while_the_step_element_is_still_at_page_level():
    still = cart("c1", 23, 176.98)
    assert verdict(still, [FORM1_NOW, still, FORM3_NOW], FORM2) is None


def test_a_frame_that_holds_the_same_widget_next_to_the_page_level_ones_is_no_move():
    still = cart("c1", 23, 176.98)
    assert verdict(still, [FORM1_NOW, still, FORM3_NOW, IN_FRAME], FORM2) is None


def test_a_pick_that_is_not_a_twin_of_the_step_element_is_not_a_frame_move():
    other = item("c9", "button", box(500, 500, 10, 10), name="Buy", data_qa="buy")
    assert verdict(other, [other, IN_FRAME], FORM2) != "moved_into_frame"
    assert verdict(other, [other, IN_FRAME], FORM2) == "different_element"       # its own hook and copy: another element


# ---- t53: a written CSS locator must end on the chosen element's tag ---------------------------------------------
def test_a_written_css_locator_that_would_end_on_another_tag_is_dropped(monkeypatch):
    import voyageur.locator_policy as lp
    inv = Inventory([{"tag": "div", "id": "switch"}], True)
    assert write(inv.elements[0], inv, "python", "page", "chain").candidates          # ends on the element: kept
    monkeypatch.setattr(lp, "subject_tag", lambda expr: "input")
    assert write(inv.elements[0], inv, "python", "page", "chain").candidates == []


def test_every_written_locator_ends_on_the_chosen_tag_or_names_none():
    from voyageur.element_facts import subject_tag
    inv = Inventory([{"tag": "input", "id": "u", "name_attr": "user", "class": "field big", "testid": "t"}], True)
    for c in write(inv.elements[0], inv, "python", "page", "chain").candidates:
        assert subject_tag(c.code["python"]) in (None, "input")
