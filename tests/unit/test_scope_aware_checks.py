"""The static checks look at the first call of a chain on the page, never at a call that is already scoped."""
import pytest

from voyageur.uniqueness import aria_count, broad_reason

pytestmark = pytest.mark.unit

YAML = '''- main:
  - table "Orders":
    - row "1042 Cobalt paid":
      - button "paid"
    - row "1043 Delta pending":
      - button "pending"
    - row "1044 Echo pending":
      - button "pending"
'''


def test_a_role_call_that_is_the_first_on_the_page_is_counted():
    assert aria_count("page.getByRole('button', { name: 'pending' })", YAML) == 2
    assert aria_count("page.get_by_role('button', name='pending').first", YAML) == 1


@pytest.mark.parametrize("expr", [
    "page.getByTestId('order-row-1043').getByRole('button', { name: 'pending' })",
    "page.getByTestId('orders-table').locator('tr[data-qa=\"order-row-1043\"]')"
    ".getByRole('button', { name: 'pending' })",
    "page.get_by_role('row').get_by_role('button', name='pending')",
    "page.locator('#orders').get_by_role('button', name='pending')",
])
def test_a_role_call_after_a_scope_is_unknown_not_counted_over_the_whole_page(expr):
    assert aria_count(expr, YAML) is None


def test_a_bare_selector_on_the_page_is_broad():
    assert broad_reason("page.locator('li')") and broad_reason("page.locator('td').first")


@pytest.mark.parametrize("expr", [
    "page.getByTestId('tab-history').locator('..').locator('li')",
    "page.getByTestId('orders-table').locator('tbody tr').nth(4).locator('td')",
    "page.get_by_test_id('x').locator('td')",
    "page.frame_locator('iframe').locator('li')",
])
def test_a_bare_selector_after_a_scope_is_not_broad(expr):
    assert broad_reason(expr) is None
