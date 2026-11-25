"""Fingerprint match: exactly one strong same-element match settles the target with no model call, else None."""
import pytest

from voyageur.match import match_fingerprint

pytestmark = pytest.mark.unit

BOX = {"x": 410, "y": 218, "width": 280, "height": 36}


def el(ref, **kw):
    return {"ref": ref, "tag": "input", "type": "text", **kw}


def test_a_stable_attribute_that_survived_settles_the_target():
    items = [el("e0", id="other"), el("e1", name_attr="user", testid="u", box=BOX)]
    m = match_fingerprint(items, {"tag": "input", "name_attr": "user", "id": "username"})
    assert m and m.element["ref"] == "e1" and "name_attr" in m.signals


def test_role_name_and_position_together_settle_it_when_no_attribute_survived():
    items = [el("e0", label="Nom", name="Username", box=BOX), el("e1", label="x", name="Other", box={"x": 5, "y": 5, "width": 9, "height": 9})]
    m = match_fingerprint(items, {"role": "textbox", "name": "Username", "box": BOX})
    assert m and m.element["ref"] == "e0" and "role+name+box" in m.signals


def test_role_and_name_without_the_same_place_is_not_enough():
    items = [el("e0", name="Username", box={"x": 900, "y": 900, "width": 5, "height": 5})]
    assert match_fingerprint(items, {"role": "textbox", "name": "Username", "box": BOX}) is None


def test_two_strong_matches_settle_nothing():
    items = [el("e0", name_attr="user"), el("e1", name_attr="user")]
    assert match_fingerprint(items, {"tag": "input", "name_attr": "user"}) is None


def test_nothing_strong_settles_nothing():
    assert match_fingerprint([el("e0", id="a")], {"tag": "input", "id": "b"}) is None
    assert match_fingerprint([], {"id": "b"}) is None
    assert match_fingerprint([el("e0", id="a")], {}) is None


def test_a_generated_id_is_not_a_stable_signal():
    assert match_fingerprint([el("e0", id=":r1:")], {"tag": "input", "id": ":r1:"}) is None


def test_hidden_elements_and_incompatible_actions_never_match():
    assert match_fingerprint([el("e0", name_attr="user", hidden=True)], {"name_attr": "user"}) is None
    cb = {"ref": "e0", "tag": "div", "name_attr": "user"}
    assert match_fingerprint([cb], {"tag": "div", "name_attr": "user"}, action="fill") is None
    assert match_fingerprint([cb], {"tag": "div", "name_attr": "user"}, action="click") is not None


def test_a_hook_many_elements_share_identifies_only_with_its_row():
    items = [{"ref": "e0", "tag": "button", "data_cy": "row-menu", "row": "r1"},
             {"ref": "e1", "tag": "button", "data_cy": "row-menu", "row": "r2"}]
    assert match_fingerprint(items, {"tag": "button", "data_cy": "row-menu"}) is None
    m = match_fingerprint(items, {"tag": "button", "data_cy": "row-menu", "row": "r2"})
    assert m and m.element["ref"] == "e1"
