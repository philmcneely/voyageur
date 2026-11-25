"""A role + name that matches nothing says what the page does have under that role."""
import pytest

from voyageur.inventory import Inventory
from voyageur.uniqueness import Uniqueness

pytestmark = pytest.mark.unit

ITEMS = [{"tag": "div", "role": "progressbar", "name": "", "hidden": False},
         {"tag": "button", "role": "", "name": "Save", "hidden": False},
         {"tag": "button", "role": "", "name": "Save draft", "hidden": False}]
ARIA = """- main:
  - progressbar
  - button "Save"
  - button "Save draft"
"""


def missing(expr):
    return Uniqueness().missing(expr, Inventory(ITEMS, True, ARIA))


def test_a_role_that_exists_without_that_name_says_so_and_names_what_it_has():
    why = missing('page.get_by_role("progressbar", name="Loading")')
    assert why and "role 'progressbar'" in why and "no accessible name" in why and "drop name" in why


def test_the_same_in_typescript():
    why = missing("page.getByRole('progressbar', { name: 'Loading' })")
    assert why and "role 'progressbar'" in why and "no accessible name" in why


def test_the_names_that_do_exist_are_listed():
    why = missing('page.get_by_role("button", name="Store")')
    assert why and "'Save'" in why and "'Save draft'" in why


def test_a_role_nothing_has_gets_the_plain_message():
    why = missing('page.get_by_role("slider", name="Volume")')
    assert why and "matches no element" in why and "role 'slider'" not in why


def test_a_very_long_accessible_name_is_cut_in_the_hint():
    aria = '- main:\n  - button "' + "x" * 5000 + '"\n'
    why = Uniqueness().missing('page.get_by_role("button", name="Save")', Inventory(ITEMS, True, aria))
    assert why and len(why) < 1200
