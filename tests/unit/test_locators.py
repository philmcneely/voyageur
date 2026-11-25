"""Unit: selector extraction, call lookup, replacement validation (heal-scope guard)."""
import pytest

from voyageur.locators import broken_selector_from, find_call, is_valid_replacement, receiver_of

pytestmark = pytest.mark.unit

SRC = "def t(self):\n    self.page.locator('#a').click()\n    page.get_by_label(\"Name\").fill(x)\n"


def test_selector_from_waiting_for():
    assert broken_selector_from("Call log:\n  - waiting for locator(\"#gone\")") == "#gone"
    assert broken_selector_from("Timeout waiting for locator '#gone'") == "#gone"


def test_selector_from_traceback_call():
    assert broken_selector_from("E   page.locator('#x').click()") == "#x"


def test_selector_with_quotes_inside():
    assert broken_selector_from("""waiting for locator('[data-x="y"]')""") == '[data-x="y"]'


def test_no_selector_means_none():
    assert broken_selector_from("assert 1 == 2") is None


def test_find_call_exact_expression():
    assert find_call(SRC, "#a") == "self.page.locator('#a')"
    assert find_call(SRC, "Name") == 'page.get_by_label("Name")'
    assert find_call(SRC, "missing") is None


def test_receiver_of():
    assert receiver_of("self.page.locator('#a')") == "self.page"
    assert receiver_of("page.get_by_label('x')") == "page"


def test_valid_replacement():
    call = "self.page.locator('#a')"
    assert is_valid_replacement(call, "self.page.get_by_role('button', name='Go')")


@pytest.mark.parametrize("bad", [
    "", "self.page.locator('#a')",                 # empty / unchanged
    "other.locator('#b')",                         # different receiver
    "self.page.locator('#b')\nassert False",       # multi-line
    "self.page.locator(",                          # syntax error
    "self.page.title()",                           # not a locator
    "assert x",                                    # statement, not expression
])
def test_invalid_replacements(bad):
    assert not is_valid_replacement("self.page.locator('#a')", bad)


def test_pick_broken_skips_selectors_absent_from_source():
    from voyageur.locators import pick_broken
    err = "E  raise X(f\"waiting for locator '{sel}'\")\nE  waiting for locator '#real'"
    assert pick_broken(err, "page.locator('#real').click()") == ("#real", "page.locator('#real')")
    assert pick_broken(err, "nothing") is None


def test_public_names_are_the_ones_other_modules_use():
    # (the underscore aliases the original kept for its older callers are not carried over)
    from voyageur import locators as L
    assert not hasattr(L, "_LIT") and not hasattr(L, "normalize_proposal") and not hasattr(L, "route_from")
    assert L.strip_strings("a('x') + \"y\"") == "a('') + ''"
    assert L.first_group(L._SELECTOR_RE.search("getByText(\"hi\")")) == "hi"
