"""Locator policy, write(): ranked, unique, round-tripped locators for one element, with a tier floor."""
import pytest

from voyageur.inventory import Inventory
from voyageur.locator_policy import STABILITY_ORDER, write
from voyageur.locator_steps import try_parse

pytestmark = pytest.mark.unit

USER = {"tag": "input", "type": "text", "id": "user", "testid": "login-user", "name_attr": "user", "label": "Username",
        "placeholder": "Your name", "box": {"x": 1, "y": 1, "width": 9, "height": 9}}
PASS = {"tag": "input", "type": "password", "id": "pw", "label": "Password", "name_attr": "pw"}
GO = {"tag": "button", "text": "Go", "name": "Go", "class": "btn primary"}
ARIA = '- textbox "Username"\n- textbox "Password"\n- button "Go"'


def inv(*items, aria=ARIA, complete=True):
    return Inventory(list(items), complete, aria)


def run(item, inventory, broken_kind="id", language="python", receiver="self.page", attr="data-testid", **kw):
    return write(inventory.elements[item], inventory, language, receiver, broken_kind, attr, **kw)


def test_candidates_come_best_first_unique_and_in_both_languages():
    out = run(0, inv(dict(USER), dict(PASS), dict(GO)), broken_kind="chain")
    kinds = [c.kind for c in out.candidates]
    assert kinds[:3] == ["testid", "id", "name"]
    top = out.candidates[0]
    assert top.code["python"] == 'self.page.get_by_test_id("login-user")'
    assert top.code["typescript"] == "this.page.getByTestId('login-user')"
    assert top.neutral[0]["m"] == "get_by_test_id" and top.neutral[0]["a"] == ["login-user"]
    assert not out.below_floor and out.reason == ""


def test_every_candidate_round_trips_through_the_chain_parser_in_both_languages():
    out = run(0, inv(dict(USER), dict(PASS), dict(GO)), broken_kind="chain")
    for c in out.candidates:
        assert try_parse(c.code["python"].replace("self.page", "page"), "python")
        assert try_parse(c.code["typescript"].replace("this.page", "page"), "typescript")


def test_the_floor_keeps_a_heal_from_moving_down_when_something_higher_is_unique():
    out = run(0, inv(dict(USER), dict(PASS)), broken_kind="id")
    assert all(c.tier <= 1 for c in out.candidates)          # testid and id only, nothing at name or below
    assert [c.kind for c in out.candidates] == ["testid", "id"]


def test_a_move_down_is_allowed_only_when_nothing_at_or_above_the_floor_is_unique_and_is_flagged():
    item = {"tag": "input", "type": "text", "label": "Username", "placeholder": "Your name"}
    out = run(0, inv(item, dict(PASS)), broken_kind="testid")
    assert out.below_floor and out.candidates and out.candidates[0].kind in ("role", "label")
    assert out.floor_kind == "testid"


def test_a_candidate_that_matches_two_elements_is_dropped():
    twin = dict(USER, id="user2", testid="other")
    out = run(0, inv(dict(USER), twin), broken_kind="chain")
    kinds = [c.kind for c in out.candidates]
    assert "label" not in kinds and "placeholder" not in kinds and "testid" in kinds


def test_structural_only_gives_no_candidate_and_says_why():
    out = run(0, inv({"tag": "div", "class": "a b c"}, {"tag": "div", "class": "a b c"}), broken_kind="id")
    assert out.candidates == [] and out.reason == "no_stable_locator"


def test_test_id_only_for_the_configured_attribute_other_hooks_become_css():
    item = {"tag": "button", "testid": "t1", "data_qa": "go-qa", "text": "Go"}
    default = run(0, inv(item, aria=""), broken_kind="chain")
    assert default.candidates[0].code["python"] == 'self.page.get_by_test_id("t1")'
    assert any(c.code["python"] == "self.page.locator(\"[data-qa='go-qa']\")" for c in default.candidates)
    qa = run(0, inv(item, aria=""), broken_kind="chain", attr="data-qa")
    assert qa.candidates[0].code["python"] == 'self.page.get_by_test_id("go-qa")'
    assert any(c.code["python"] == "self.page.locator(\"[data-testid='t1']\")" for c in qa.candidates)
    assert not any("get_by_test_id(\"t1\")" in c.code["python"] for c in qa.candidates)


def test_an_attribute_the_oracle_cannot_read_never_becomes_get_by_test_id():
    item = {"tag": "button", "testid": "t1", "text": "Go"}
    out = run(0, inv(item, aria=""), broken_kind="chain", attr="data-weird")
    assert not any("get_by_test_id" in c.code["python"] for c in out.candidates)


def test_role_candidates_need_the_aria_snapshot_to_prove_they_are_unique():
    item = {"tag": "button", "name": "Go", "text": "Go"}
    without = run(0, inv(item, aria=""), broken_kind="chain")
    assert "role" not in [c.kind for c in without.candidates]
    with_aria = run(0, inv(item, aria='- button "Go"'), broken_kind="chain")
    assert "role" in [c.kind for c in with_aria.candidates]


def test_copy_based_flag_marks_role_label_placeholder_and_text():
    out = run(0, inv({"tag": "input", "type": "text", "label": "Username"}), broken_kind="chain")
    assert out.candidates and all(c.copy_based for c in out.candidates)
    out = run(0, inv(dict(USER)), broken_kind="chain")
    assert not out.candidates[0].copy_based


def test_the_broken_call_is_validated_against_each_candidate():
    out = run(0, inv(dict(USER)), broken_kind="chain", broken_call='self.page.get_by_test_id("login-user")')
    assert 'self.page.get_by_test_id("login-user")' not in [c.code["python"] for c in out.candidates]


def test_a_hostile_tag_is_never_written_into_a_selector():
    item = {"tag": "input[x]", "name_attr": "q"}
    out = run(0, inv(item, aria=""), broken_kind="chain")
    assert all("input[x]" not in c.code["python"] for c in out.candidates)


def test_a_typescript_client_gets_typescript_first():
    out = run(0, inv(dict(USER)), broken_kind="chain", language="typescript", receiver="this.page")
    assert out.candidates[0].code["typescript"].startswith("this.page.getByTestId")
    assert out.candidates[0].code["python"].startswith("self.page.get_by_test_id")


def test_a_custom_receiver_keeps_its_own_name_in_the_client_language():
    out = run(0, inv(dict(USER)), broken_kind="chain", receiver="self.form")
    assert out.candidates[0].code["python"].startswith("self.form.")
    assert out.candidates[0].code["typescript"].startswith("this.form.")


def test_the_default_order_is_the_full_stability_order():
    assert STABILITY_ORDER[0] == "testid" and "text" in STABILITY_ORDER


# ---- plain data-* attributes (data-menu, data-agent, data-name): a css locator when nothing hooked is unique -------
MENU_SORT = {"tag": "mat-menu", "data_menu": "sort", "box": {"x": 0, "y": 0, "width": 9, "height": 9}}
MENU_TAG = {"tag": "mat-menu", "data_menu": "tag", "box": {"x": 0, "y": 20, "width": 9, "height": 9}}


def test_a_plain_data_attribute_is_written_as_a_css_locator_on_the_tag():
    out = run(1, inv(dict(MENU_SORT), dict(MENU_TAG), aria=""), broken_kind="chain")
    assert [c.kind for c in out.candidates] == ["css"]
    assert out.candidates[0].code["python"] == 'self.page.locator("mat-menu[data-menu=\'tag\']")'
    assert out.candidates[0].code["typescript"] == "this.page.locator('mat-menu[data-menu=\"tag\"]')"
    assert not out.candidates[0].copy_based


def test_a_data_attribute_two_elements_share_is_not_written():
    out = run(1, inv(dict(MENU_SORT, data_menu="tag"), dict(MENU_TAG), aria=""), broken_kind="chain")
    assert out.candidates == [] and out.reason == "no_stable_locator"


@pytest.mark.parametrize("extra", [
    {"data_state": "open"}, {"data_active": "true"}, {"data_selected": "x"}, {"data_index": "3"},
    {"data_menu": "3"}, {"data_menu": "true"}, {"data_menu": "closed"}, {"data_menu": "a1b2c3d4e5"},
    {"data_menu": "row-20261107"}, {"data_menu": ":r2u:"}, {"data_menu": "x" * 80}, {"data_menu": ""},
    {"data_reactid": "tag"}, {"data_radix_collection_item": "tag"}, {"data_v_1a2b3c": "tag"},
])
def test_a_state_positional_generated_or_framework_data_attribute_is_never_written(extra):
    target = {"tag": "mat-menu", "box": {"x": 0, "y": 20, "width": 9, "height": 9}, **extra}
    out = run(1, inv(dict(MENU_SORT, data_menu="sort"), target, aria=""), broken_kind="chain")
    assert out.candidates == [], out.candidates


def test_a_data_attribute_ranks_below_hooks_ids_and_names():
    item = {"tag": "input", "id": "qty", "data_field": "quantity", "box": {"x": 0, "y": 0, "width": 9, "height": 9}}
    out = run(0, inv(item, aria=""), broken_kind="chain")
    assert [c.kind for c in out.candidates] == ["id", "css"]
    assert out.candidates[1].code["python"] == 'self.page.locator("input[data-field=\'quantity\']")'


def test_a_hyphenated_tag_with_an_attribute_is_a_css_kind():
    from voyageur.locator_policy import kind_of
    assert kind_of("page.locator('mat-menu[data-menu=\"tag\"]')") == "css"
    assert kind_of("page.locator('mat-menu')") == "css"


def test_a_value_the_probe_may_have_cut_in_utf16_units_is_not_written():
    target = {"tag": "mat-menu", "data_menu": "\U0001F600" * 40, "box": {"x": 0, "y": 20, "width": 9, "height": 9}}
    assert run(1, inv(dict(MENU_SORT), target, aria=""), broken_kind="chain").candidates == []


def test_data_testid_sent_as_a_plain_data_key_is_not_a_css_candidate():
    target = {"tag": "mat-menu", "data_testid": "tag", "box": {"x": 0, "y": 20, "width": 9, "height": 9}}
    assert run(1, inv(dict(MENU_SORT), target, aria=""), broken_kind="chain").candidates == []


def test_a_hyphenated_tag_name_locator_is_a_name_kind():
    from voyageur.locator_policy import kind_of
    assert kind_of("page.locator('mat-input[name=\"q\"]')") == "name"


def test_a_hook_value_spelling_a_test_id_call_does_not_borrow_the_test_id_proof():
    mine = {"tag": "div", "testid": "row-a", "data_qa": "get_by_test_id", "box": {"x": 0, "y": 0, "width": 9, "height": 9}}
    twin = {"tag": "div", "data_qa": "get_by_test_id", "box": {"x": 0, "y": 20, "width": 9, "height": 9}}
    codes = [c.code["python"] for c in run(0, inv(mine, twin, aria=""), broken_kind="chain").candidates]
    assert codes == ['self.page.get_by_test_id("row-a")']


# ---- copy_only elements and labels -----------------------------------------------------------------------------------
LABEL = {"tag": "label", "for_attr": "birthdate", "text": "What's the patient's birth date?", "name": "What's the patient's birth date?"}
DATE = {"tag": "input", "type": "date", "id": "birthdate", "label": "What's the patient's birth date?"}
CHAT = {"tag": "div", "text": "Hi there, is the build green?", "name": "Hi there, is the build green?", "copy_only": True}


def test_a_label_is_written_by_the_control_it_names_ahead_of_its_copy():
    inventory = inv(dict(LABEL), dict(DATE), aria='- text: What\'s the patient\'s birth date?\n- textbox')
    out = run(0, inventory, broken_kind="chain")
    assert out.candidates[0].code["python"] == 'self.page.locator("label[for=\'birthdate\']")'
    assert out.candidates[0].code["typescript"] == "this.page.locator('label[for=\"birthdate\"]')"
    assert out.candidates[0].kind == "css" and not out.candidates[0].copy_based


def test_a_label_naming_a_generated_id_is_written_by_its_copy_only():
    label = {**LABEL, "for_attr": ":r1:"}
    out = run(0, inv(label, {**DATE, "id": ":r1:"}, aria="- text: What's the patient's birth date?"), broken_kind="chain")
    assert [c.kind for c in out.candidates] == ["text"]


def test_two_labels_for_the_same_control_are_not_told_apart_by_the_for():
    out = run(0, inv(dict(LABEL), {**LABEL, "text": "Other"}, dict(DATE), aria="- text: What's the patient's birth date?"),
              broken_kind="chain")
    assert all(c.kind != "css" for c in out.candidates)


def test_a_copy_only_element_is_written_by_its_exact_text_when_nothing_stabler_exists():
    out = run(0, inv(dict(CHAT), dict(GO), aria="- text: Hi there, is the build green?\n- button \"Go\""), broken_kind="chain")
    assert [c.kind for c in out.candidates] == ["text"] and out.candidates[0].copy_based and not out.below_floor
    assert out.candidates[0].code["python"] == 'self.page.get_by_text("Hi there, is the build green?", exact=True)'
    assert out.candidates[0].code["typescript"] == "this.page.getByText('Hi there, is the build green?', { exact: true })"


def test_a_copy_only_element_whose_text_is_on_the_page_twice_is_not_written_by_it():
    twin = {**CHAT, "box": {"x": 1, "y": 9, "width": 9, "height": 9}}
    out = run(0, inv(dict(CHAT), twin, aria="- text: Hi there, is the build green?\n- text: Hi there, is the build green?"),
              broken_kind="chain")
    assert out.candidates == [] and out.reason == "no_stable_locator"


@pytest.mark.parametrize("cls", ["css-1xzq8d1-indicatorContainer", "css-b62m3t-container", "css-13cymwt-control", "css-tj5bde-Svg"])
def test_an_emotion_class_with_a_component_label_is_generated_like_the_bare_hash(cls):
    from voyageur.locator_policy import usable_class
    assert not usable_class(cls)
