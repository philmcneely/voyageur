"""data-* attributes are written the way HTML spells them everywhere the model reads or writes one."""
import pytest

from voyageur.locators import validate_replacement

pytestmark = pytest.mark.unit

@pytest.mark.parametrize("bad, right", [
    ("page.locator(\"[data_qa='order-row-1043'] [data_qa='status']\")", "data-qa"),
    ("page.locator('[data_col=customer]')", "data-col"),
    ("page.locator('[data_status_code]')", "data-status-code"),
    ("page.locator(\"//td[@data_col='customer']\")", "data-col"),
])
def test_an_underscore_data_attribute_is_refused_naming_the_real_one(bad, right):
    why = validate_replacement("page.locator('#x')", bad, "python", None)
    assert why and f"the attribute is {right}" in why


def test_the_same_in_typescript():
    why = validate_replacement("page.locator('#x')", "page.locator('[data_event=e301]')", "typescript", None)
    assert why and "the attribute is data-event" in why


@pytest.mark.parametrize("ok", ["page.locator(\"[data-qa='status']\")", "page.get_by_text('data_qa')",
                                "page.get_by_label('Report [data_x] total')"])
def test_a_real_data_attribute_and_copy_that_mentions_the_key_are_not_refused(ok):
    assert validate_replacement("page.locator('#x')", ok, "python", None) is None


def test_a_long_run_of_backslashes_in_a_selector_is_refused_or_passed_quickly():
    import time
    t0 = time.time()
    validate_replacement("page.locator('#x')", 'page.locator("' + "\\" * 400, "python", None)
    assert time.time() - t0 < 0.5
