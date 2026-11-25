"""Language branches: copy-based locators in a page language that differs from the passing run get one locator per
language and switching code; copy is never translated; a non-copy locator wins with no branch."""
import pytest

from voyageur.branches import plan
from voyageur.inventory import Inventory
from voyageur.locator_policy import write

pytestmark = pytest.mark.unit

FR_INPUT = {"tag": "input", "type": "text", "label": "Nom d'utilisateur"}
FINGERPRINT = {"tag": "input", "role": "textbox", "name": "Username", "label": "Username"}


def candidate_for(item, broken_kind="label", language="python", receiver="self.page", aria=""):
    inv = Inventory([item], True, aria)
    return write(inv.elements[0], inv, language, receiver, broken_kind).candidates[0]


def test_same_language_needs_no_branch():
    c = candidate_for(FR_INPUT)
    p = plan(c, "python", "self.page", 'self.page.get_by_label("Username", exact=True)', "en", "en", FINGERPRINT)
    assert not p.needed and p.code is None


def test_a_copy_locator_in_another_language_gets_a_branch_per_language_from_recorded_copy():
    c = candidate_for(FR_INPUT)
    broken = 'self.page.get_by_label("Username", exact=True)'
    p = plan(c, "python", "self.page", broken, "en", "fr", FINGERPRINT)
    assert p.needed and p.reason == ""
    assert p.branches == {"en": broken, "fr": 'self.page.get_by_label("Nom d\'utilisateur", exact=True)'}
    assert p.code == ('by_language(self.page, {"en": ' + broken + ', "fr": '
                      + p.branches["fr"] + '})')
    assert set(p.neutral["branches"]) == {"en", "fr"}


def test_typescript_switching_code_awaits_the_helper():
    c = candidate_for(FR_INPUT, language="typescript", receiver="this.page")
    broken = "this.page.getByLabel('Username', { exact: true })"
    p = plan(c, "typescript", "this.page", broken, "en", "fr", FINGERPRINT)
    assert p.code.startswith("byLanguage(this.page, { en: ") and "fr: this.page.getByLabel(" in p.code


def test_the_old_branch_comes_from_the_fingerprint_when_the_broken_locator_is_not_copy_based():
    c = candidate_for(FR_INPUT, broken_kind="id")
    p = plan(c, "python", "self.page", 'self.page.locator("#user")', "en", "fr", FINGERPRINT)
    assert p.needed and p.branches["en"] == 'self.page.get_by_label("Username", exact=True)'


def test_no_old_copy_means_no_branch_can_be_built_and_the_plan_says_so():
    c = candidate_for(FR_INPUT, broken_kind="id")
    p = plan(c, "python", "self.page", 'self.page.locator("#user")', "en", "fr", None)
    assert p.needed and p.code is None and p.reason == "language_branch_unavailable"


def test_a_non_copy_locator_wins_with_no_branch():
    item = dict(FR_INPUT, id="user")
    c = candidate_for(item, broken_kind="id")
    assert not c.copy_based
    assert not plan(c, "python", "self.page", 'self.page.locator("#old")', "en", "fr", FINGERPRINT).needed


def test_unknown_languages_or_a_switch_turned_off_need_no_branch():
    c = candidate_for(FR_INPUT)
    broken = 'self.page.get_by_label("Username", exact=True)'
    assert not plan(c, "python", "self.page", broken, "", "fr", FINGERPRINT).needed
    assert not plan(c, "python", "self.page", broken, "en", "", FINGERPRINT).needed
    assert not plan(c, "python", "self.page", broken, "en", "fr", FINGERPRINT, enabled=False).needed


def test_a_language_that_is_not_a_plain_subtag_is_never_written_as_a_key():
    c = candidate_for(FR_INPUT)
    broken = 'self.page.get_by_label("Username", exact=True)'
    assert not plan(c, "python", "self.page", broken, "en", "fr\"}); evil(", FINGERPRINT).needed


def test_identical_copy_in_both_languages_needs_no_branch():
    c = candidate_for({"tag": "button", "name": "OK", "text": "OK"}, broken_kind="role", aria='- button "OK"')
    broken = 'self.page.get_by_role("button", name="OK", exact=True)'
    assert not plan(c, "python", "self.page", broken, "en", "fr", FINGERPRINT).needed
