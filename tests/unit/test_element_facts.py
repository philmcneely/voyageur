"""Element facts: an entry split into what it is and what it says, and the tag a CSS locator ends on."""
import pytest

from voyageur.element_facts import copy_of, element_kind, subject_tag

pytestmark = pytest.mark.unit


def test_kind_drops_copy_and_layout_and_empty_values():
    e = {"tag": "button", "type": "", "name": "Go", "label": "L", "id": "x", "hidden": False,
         "box": {"x": 1, "y": 1, "width": 1, "height": 1}, "frame": "f", "data_qa": "q"}
    assert element_kind(e) == {"tag": "button", "id": "x", "data_qa": "q"}
    assert copy_of(e) == {"name": "Go", "label": "L"}


@pytest.mark.parametrize("chain", [
    "page.locator('div[data-name=\"collection\"] input[type=\"checkbox\"]')",
    "page.locator('div[data-name=\"collection\"] > input.woo-switch-input')",
    "page.locator('form').locator('input[type=\"checkbox\"]')",
    "page.locator('div[data-name=\"collection\"]').locator('input')",
    "page.locator('div[data-name=\"collection\"]').locator('input').first",
])
def test_the_subject_tag_is_read_from_the_last_selector_of_any_chain(chain):
    assert subject_tag(chain) == "input"


@pytest.mark.parametrize("chain", [
    "page.locator('div[data-name=\"collection\"] *')",
    "page.locator('div[data-name=\"collection\"] [type=\"checkbox\"]')",
    "page.locator('#x')", "page.get_by_label('x')", "page.locator('xpath=//input')", "page.locator('a, b')", "not a locator",
])
def test_no_subject_tag_when_the_chain_does_not_say(chain):
    assert subject_tag(chain) is None


def test_the_own_tag_chains():
    assert subject_tag("page.locator('div[class*=\"_switch_\"][data-name=\"collection\"]')") == "div"
    assert subject_tag("page.locator('form div[data-name=\"collection\"]')") == "div"
