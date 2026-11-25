"""Input caps: over-cap text is refused at intake or answered "cannot evaluate", never cut and then compared."""
import time

import pytest

from voyageur import bounds, inventory, rx, uniqueness
from voyageur.failing import iter_calls
from voyageur.schemas import LimitExceeded, check_limits, parse_request

pytestmark = pytest.mark.unit

BOUND_S = 2.0


def _req(**over):
    base = {"client": {"framework": "playwright", "language": "typescript"}, "test": {"id": "t", "title": "t"},
            "failure": {"error": "waiting for locator('#a')"}, "source": {"files": []},
            "page": {"url": "http://x/"}}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    return base


def test_the_request_helper_builds_a_valid_request():
    parse_request(_req())


def test_error_text_over_64kb_is_refused():
    with pytest.raises(ValueError):
        parse_request(_req(failure={"error": "x\n" * (bounds.MAX_ERROR_CHARS // 2 + 1)}))


def test_one_error_line_over_the_cap_is_refused():
    with pytest.raises(ValueError):
        parse_request(_req(failure={"error": "a" * (bounds.MAX_LINE_CHARS + 1)}))


def test_an_error_at_the_caps_is_accepted():
    parse_request(_req(failure={"error": ("a" * 1000 + "\n") * 60}))


def test_one_aria_line_over_the_cap_is_413():
    with pytest.raises(LimitExceeded):
        check_limits({"page": {"aria_snapshot": '- button "' + "a" * (bounds.MAX_ARIA_LINE_CHARS + 1) + '"'}})


def test_an_inventory_field_over_the_cap_is_refused_not_cut():
    item = {"tag": "button", "text": "a" * (bounds.MAX_FIELD_CHARS + 1)}
    with pytest.raises(ValueError):
        parse_request(_req(page={"inventory": [item]}))


def test_a_fingerprint_attribute_over_the_cap_is_refused_not_cut():
    fp = {"tag": "button", "attrs": {"data-x": "a" * (bounds.MAX_FIELD_CHARS + 1)}}
    with pytest.raises(ValueError):
        parse_request(_req(last_pass={"fingerprint": fp}))


def test_an_inventory_with_an_over_cap_field_is_not_complete():
    inv = inventory.Inventory([{"tag": "button", "name": "ok"}, {"tag": "button", "text": "a" * 500}], True)
    assert inv.complete is False
    assert inv.count("page.getByPlaceholder('nothing')") is None


def test_over_long_expressions_are_unknown_everywhere():
    big = "page.getByText('" + "a" * (bounds.MAX_EXPR_CHARS + 1) + "')"
    inv = inventory.Inventory([{"tag": "button", "text": "a"}], True)
    assert inv.count(big) is None and inv.matched(big) is None
    assert uniqueness.aria_count(big, '- button "a"') is None
    assert uniqueness.broad_reason(big) is None


def test_a_call_on_a_source_line_over_the_cap_is_not_read():
    line = "x = 1; " * (bounds.MAX_SOURCE_LINE_CHARS // 7 + 10) + "page.locator('#a').click()"
    assert iter_calls(line) == []
    assert len(iter_calls("page.locator('#a').click()\n" + line)) == 1


def test_a_chain_of_many_filters_is_read_within_a_budget():
    src = "page.locator('a')" + ".filter({ has: page.locator('b') })" * 3000
    started = time.monotonic()
    iter_calls(src)
    assert time.monotonic() - started < BOUND_S


# ---- rx ---------------------------------------------------------------------------------------------------------------
def test_a_text_over_the_cap_is_unknown_not_truncated():
    with pytest.raises(rx.CannotEvaluate):
        rx.search("b$", "", "a" * (rx.MAX_TEXT_CHARS - 1) + "xb")        # a cut would have made `b$` match


def test_simple_patterns_still_count():
    inv = inventory.Inventory([{"tag": "button", "text": "Submit now"}, {"tag": "a", "text": "other"}], True)
    assert inv.count("page.getByText(/^sub/i)") == 1


def test_flags_m_and_s_map_and_unknown_flags_are_unknown():
    assert rx.search("^b", "m", "a\nb") is True
    assert rx.search("a.b", "s", "a\nb") is True
    with pytest.raises(rx.CannotEvaluate):
        rx.search("a", "x", "a")


def test_repeat_bounds_multiply():
    with pytest.raises(rx.CannotEvaluate):
        rx.search("((a{0,50}){0,9})*X", "", "a")
    with pytest.raises(rx.CannotEvaluate):
        rx.search("(.{0,900}|b)*X", "", "b")


def test_nfa_pattern_over_many_long_texts_is_unknown_and_fast():
    inv = inventory.Inventory([{"tag": "button", "text": "b" * 400}] * 500, True)
    started = time.monotonic()
    assert inv.count("page.getByText(/(.{0,900}|b)*X/)") is None
    snap = "".join('- button "' + "b" * 400 + '"\n' for _ in range(500))
    assert uniqueness.aria_count("page.getByRole('button', { name: /(.{0,900}|b)*X/ })", snap) is None
    assert time.monotonic() - started < BOUND_S


def test_the_work_budget_is_shared_by_nested_calls_and_spent_means_unknown():
    with rx.work_budget():
        with rx.work_budget():
            for _ in range(rx.WORK_BUDGET_CHARS // rx.MAX_TEXT_CHARS):
                rx.search("x", "", "a" * rx.MAX_TEXT_CHARS)
            with pytest.raises(rx.CannotEvaluate):
                rx.search("x", "", "a" * 1000)
    assert rx.search("x", "", "a" * 1000) is False                                 # outside the block: no budget
