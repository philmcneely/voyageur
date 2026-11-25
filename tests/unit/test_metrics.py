"""Metrics: the spec's headline numbers with exact definitions, computed from the heal record."""
import pytest

from voyageur.metrics import aggregate
from voyageur.store import Store

pytestmark = pytest.mark.unit


def seed():
    s = Store(":memory:", 14, lambda: 1000.0)

    def heal(hid, **kw):
        s.create_heal(hid, repo="app", framework="fw", **kw)

    def call(hid, tokens_in, tokens_out, local=True, model="m", role="text", ms=10):
        s.add_call(hid, role=role, model=model, local=local, input_tokens=tokens_in, output_tokens=tokens_out, images=0,
                   latency_ms=ms, ok=True, step=3)
    heal("a", drift=1, outcome="healed", settled_at=3, ended_at=7, covers=3, locator_key="p.py|x", file="p.py",
         tier_movement="up", page_language="fr", branch=1, wrong_element=1, duration_ms=100, pr_state="merged_unchanged")
    call("a", 100, 10)
    call("a", 50, 5)
    heal("b", drift=1, outcome="healed", settled_at=2, ended_at=7, covers=1, locator_key="p.py|y", file="p.py",
         tier_movement="same", page_language="en", duration_ms=10, pr_state="edited")
    heal("c", drift=1, outcome="needs_human", reason="budget_exhausted", ended_at=3, budget_exhausted=1, duration_ms=30)
    call("c", 200, 0, local=False, model="big")
    heal("d", drift=1, outcome="needs_human", reason="no_pick", ended_at=3, duration_ms=50)
    heal("e", drift=0, outcome="not_drift", reason="no_locator_in_error", ended_at=1, duration_ms=1)
    heal("f", drift=1, outcome="try", duration_ms=0)
    return s


def test_headline_numbers_have_the_stated_definitions():
    m = aggregate(seed(), prices={"m": (1.0, 2.0)})
    assert m["failures_seen"] == 6 and m["locator_drift"] == 5 and m["not_drift"] == 1
    assert m["healed"] == 2 and m["needs_human"] == 2 and m["open"] == 1
    assert m["success_rate"] == pytest.approx(2 / 5) and m["fail_rate"] == pytest.approx(2 / 5)
    assert m["needs_human_by_reason"] == {"budget_exhausted": 1, "no_pick": 1}
    assert m["tests_fixed"] == 4 and m["locators_fixed"] == 2
    assert m["model_calls"] == 3 and m["calls_per_fixed_test"] == pytest.approx(3 / 4)
    assert m["calls_that_led_to_fix"] == 2 and m["calls_that_led_to_fix_rate"] == pytest.approx(2 / 3)
    assert (m["input_tokens"], m["output_tokens"]) == (350, 15) and m["tokens_per_fix"] == pytest.approx(365 / 4)
    assert m["kept_rate"] == pytest.approx(1 / 2) and m["escaped_or_edited"] == 1


def test_cost_is_reported_only_when_every_model_used_has_a_price():
    s = seed()
    assert aggregate(s, prices={"m": (1.0, 2.0)})["cost"] is None
    m = aggregate(s, prices={"m": (1.0, 2.0), "big": (10.0, 0.0)})
    assert m["cost"] == pytest.approx((150 * 1 + 15 * 2 + 200 * 10) / 1e6)
    assert m["cost_per_fix"] == pytest.approx(m["cost"] / 4)


def test_the_detail_breakdowns():
    m = aggregate(seed())
    assert m["settled_at"] == {"2": 1, "3": 1} and m["ended_at"]["7"] == 2 and m["ended_at"]["1"] == 1
    assert m["tier_movement"] == {"up": 1, "same": 1, "down": 0}
    assert m["language"] == {"fr": 1, "en": 1} and m["branches_added"] == 1 and m["wrong_element_catches"] == 1
    assert m["hot_spots"][0]["file"] == "p.py"
    assert m["local_token_share"] == pytest.approx(165 / 365) and m["budget_exhausted"] == 1
    assert m["outcome_mix"] == {"healed": 2, "needs_human": 2, "not_drift": 1, "try": 1}
    assert {r["model"] for r in m["cost_by_model"]} == {"m", "big"} and m["vision"]["calls"] == 0
    assert m["latency"]["heal_ms_p50"] is not None and m["latency"]["model_ms_avg"] == 10


def test_an_empty_store_gives_none_not_a_division_by_zero():
    m = aggregate(Store(":memory:", 14))
    assert m["failures_seen"] == 0 and m["success_rate"] is None and m["calls_per_fixed_test"] is None
    assert m["kept_rate"] is None and m["tokens_per_fix"] is None and m["latency"]["heal_ms_p50"] is None


def test_filters_narrow_the_window():
    s = seed()
    assert aggregate(s, repo="nobody")["failures_seen"] == 0
    assert aggregate(s, framework="fw")["failures_seen"] == 6
    assert aggregate(s, since=2000.0)["failures_seen"] == 0
