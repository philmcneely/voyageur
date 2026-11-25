"""Unit: a frame hop resolves by what its selector says, operator included, and keeps its position modifier."""
import pytest

from voyageur.frames import frame_hop, resolve_frame

pytestmark = pytest.mark.unit

STRIPE = [{"tag": "iframe", "name_attr": "__privateStripeFrame1234"}, {"tag": "input", "frame": "__privateStripeFrame1234"}]


@pytest.mark.parametrize("selector, want", [
    ('iframe[name^="__privateStripeFrame"]', "__privateStripeFrame1234"),
    ('iframe[name$="Frame1234"]', "__privateStripeFrame1234"),
    ('iframe[name*="Stripe"]', "__privateStripeFrame1234"),
    ('iframe[name="__privateStripeFrame1234"]', "__privateStripeFrame1234"),
    ('iframe[name="__privateStripeFrame"]', None),          # equality is still equality
    ('iframe[name^="Stripe"]', None),
])
def test_attribute_operators_are_honoured(selector, want):
    assert resolve_frame(selector, STRIPE) == want


def test_a_word_match_needs_the_whole_word():
    items = [{"tag": "iframe", "id": "pay", "title": "card entry form"}, {"tag": "input", "frame": "pay"}]
    assert resolve_frame('iframe[title~="entry"]', items) == "pay"
    assert resolve_frame('iframe[title~="ent"]', items) is None


@pytest.mark.parametrize("call, prefix", [
    ("""page.frame_locator('iframe[name^="s"]').first.locator('[name="number"]')""",
     """page.frame_locator('iframe[name^="s"]').first"""),
    ("""page.frame_locator('iframe').nth(1).locator('x')""", "page.frame_locator('iframe').nth(1)"),
    ("""page.frameLocator('iframe').first().locator('x')""", "page.frameLocator('iframe').first()"),
    ("""page.frame_locator('#f').locator('x')""", "page.frame_locator('#f')"),
])
def test_the_hop_keeps_its_position_modifier(call, prefix):
    assert frame_hop(call)[0] == prefix


@pytest.mark.parametrize("prefix, receiver, language, py, ts", [
    ('self.page.frame_locator("iframe").first', "self.page", "python",
     'self.page.frame_locator("iframe").first.locator(', 'this.page.frameLocator("iframe").first().locator('),
    ('this.page.frameLocator("iframe").last()', "this.page", "typescript",
     'self.page.frame_locator("iframe").last.locator(', 'this.page.frameLocator("iframe").last().locator('),
    ('self.page.frame_locator("iframe").nth(1)', "self.page", "python",
     'self.page.frame_locator("iframe").nth(1).locator(', 'this.page.frameLocator("iframe").nth(1).locator('),
])
def test_a_position_on_the_hop_is_written_in_each_language(prefix, receiver, language, py, ts):
    from voyageur.frames import prefixed
    from voyageur.locator_policy import Written
    w = Written("css", 3, {"python": 'self.page.locator("input[name=\'a\']")', "typescript": "this.page.locator(\"input[name='a']\")"})
    out = prefixed(w, prefix, receiver, language)
    assert out is not None and out.code["python"].startswith(py) and out.code["typescript"].startswith(ts)


def test_a_class_named_first_inside_the_selector_is_left_alone():
    from voyageur.frames import _swap_hop_names
    assert _swap_hop_names("""self.page.frame_locator('iframe.first').first""") == \
        """self.page.frameLocator('iframe.first').first()"""
