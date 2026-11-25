"""A refusal for the other language's method spelling names the right spelling, so the model can correct itself."""
import pytest

from voyageur.locators import validate_replacement

pytestmark = pytest.mark.unit


def test_a_python_method_in_a_typescript_test_is_refused_with_the_typescript_spelling():
    why = validate_replacement("page.getByLabel('Name')", "page.get_by_role('button', { name: 'Paid' })", "typescript")
    assert why and "get_by_role" in why and "getByRole" in why and "Python" in why


def test_a_chained_python_method_in_a_typescript_test_too():
    why = validate_replacement("page.getByLabel('Name')",
                               "page.get_by_role('group', { name: 'Status' }).get_by_role('button')", "typescript")
    assert why and "getByRole" in why


def test_a_typescript_method_in_a_python_test_is_refused_with_the_python_spelling():
    why = validate_replacement("page.get_by_label('Name')", "page.getByRole('button', name='Paid')", "python")
    assert why and "get_by_role" in why and "TypeScript" in why


def test_the_right_spelling_is_not_refused():
    assert validate_replacement("page.getByLabel('Name')", "page.getByRole('button', { name: 'Paid' })",
                                "typescript") is None
    assert validate_replacement("page.get_by_label('Name')", "page.get_by_role('button', name='Paid')") is None


@pytest.mark.parametrize("proposal", [
    "page.locator('#payment-frame').frameLocator().getByLabel('Billing ZIP')",
    "page.frameLocator().getByLabel('x')",
    "page.getByRole()",
    "page.locator('#a').filter()",
])
def test_a_locator_call_with_no_argument_is_refused_in_typescript(proposal):
    why = validate_replacement("page.getByLabel('Name')", proposal, "typescript")
    assert why and "needs an argument" in why


@pytest.mark.parametrize("proposal", [
    "page.frame_locator().get_by_label('x')",
    "page.locator('#a').frame_locator().get_by_label('x')",
    "page.get_by_role()",
])
def test_a_locator_call_with_no_argument_is_refused_in_python(proposal):
    why = validate_replacement("page.get_by_label('Name')", proposal, "python")
    assert why and "needs an argument" in why


def test_calls_with_arguments_and_the_argument_free_members_are_not_refused():
    assert validate_replacement("page.getByLabel('Name')", "page.frameLocator('#f').getByLabel('x').first()",
                                "typescript") is None
    assert validate_replacement("page.get_by_label('Name')", "page.frame_locator('#f').get_by_label('x').first") is None
