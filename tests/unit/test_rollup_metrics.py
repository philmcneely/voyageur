"""The rollup keeps the totals: /v1/metrics, the by-model rows and the Prometheus counters read the same after old records go."""
import math

import pytest
from fastapi.testclient import TestClient

from voyageur.app import create_app
from voyageur.config import ServiceSettings
from voyageur.core import HealSession
from voyageur.metrics import aggregate
from voyageur.store import DAY, Store
from tests.unit.scenarios import ENDPOINT, JSON, FakeModel

pytestmark = pytest.mark.unit

T0 = 2_000_000.0
PRICES = {"small": (1.0, 2.0), "big": (10.0, 20.0)}
# computed from the retained records only: a distinct count, a list of locators or runs, and percentiles cannot be added up
RETAINED_ONLY = {"runs", "hot_spots", "locators_fixed", "latency", "truncated", "archive"}
DRILL_RETAINED_ONLY = {"runs", "hot_spots"}


def populate(store, clock):
    """Forty heals over 60 days: every mode and outcome, two repos, local and cloud calls, a heal that used two models and
    one that used none, PR follow-ups of every state."""
    outcomes = ["healed", "healed", "needs_human", "not_drift", "healed", "try", "needs_human", "healed"]
    reasons = ["no_pick", "target_missing", None, "no_stable_locator"]
    states = [None, "merged_unchanged", "edited", "reverted", "closed_unmerged", "open", "merged_unchanged"]
    for i in range(40):
        clock["t"] = T0 + i * 1.5 * DAY
        mode = "advise" if i % 11 == 5 else "diagnose" if i % 13 == 7 else "heal"
        outcome = outcomes[i % len(outcomes)]
        if mode == "advise":
            outcome = ["advised", "needs_human", "not_drift"][i % 3]
        if mode == "diagnose":
            outcome = ["diagnosed", "needs_human", "not_applicable"][i % 3]
        fields = dict(
            run_id=f"run{i % 5}", repo="alpha" if i % 3 else "beta", framework="fw1" if i % 2 else "fw2",
            language="python" if i % 4 else "typescript", test_id=f"t{i}", file=f"f{i % 4}.py", locator_key=f"f{i % 4}.py|c{i % 6}",
            page_language="en" if i % 5 else "fr", drift=int(outcome != "not_drift"), outcome=outcome, mode=mode,
            reason=reasons[i % 4] if outcome == "needs_human" else None, settled_at=3 if outcome == "healed" else None,
            ended_at=3 if outcome in ("healed", "needs_human") else 1, covers=1 + i % 3, wrong_element=i % 4 == 1,
            cache_hit=int(i % 6 == 0), budget_exhausted=int(i % 9 == 0), branch=int(i % 7 == 0), verify_passed=int(i % 2 == 0),
            tier_movement=["up", "same", "down"][i % 3], duration_ms=100 + i)
        if outcome == "healed":
            state = states[i % len(states)]
            fields.update(pr_state=state, followup_at=1.0 if state else None, runs_since=12 if i % 2 else 3,
                          days_since=31 if i % 2 else 4, failed_again=int(i % 5 == 0 and state is not None))
        if mode == "diagnose":
            fields.update(confidence=["high", "medium", "low"][i % 3], patch_suggested=int(i % 2), downgraded=int(i % 4 == 0))
        store.create_heal(f"h{i:02d}", **fields)
        n_calls = i % 4                       # 0 calls: no model; 1: small; 2: small + big (a vision call); 3: two small
        for k in range(n_calls):
            big = n_calls == 2 and k == 1
            store.add_call(f"h{i:02d}", role="vision" if big else "text", model="big" if big else "small", local=not big,
                           input_tokens=100 + i, output_tokens=10 + k, images=int(big), latency_ms=20 + i, ok=True, step=4 if big else 3)
        for r in range(i % 3):
            store.add_try(f"h{i:02d}", r, "e1", "testid", "x", "wrong_element" if r == 1 else "ok")
    clock["t"] = T0 + 40 * 1.5 * DAY


def pair(retention=31):
    """The same data in a store that never purges and in one that purges after `retention` days."""
    whole_clock, cut_clock = {"t": T0}, {"t": T0}
    whole = Store(":memory:", 100000, lambda: whole_clock["t"])
    cut = Store(":memory:", retention, lambda: cut_clock["t"])
    populate(whole, whole_clock)
    populate(cut, cut_clock)
    return whole, cut


def same(a, b, path=""):
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), f"{path}: keys {sorted(set(a) ^ set(b))}"
        for k in a:
            same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, f"{path}[{i}]")
    elif isinstance(a, float) and isinstance(b, float):
        assert math.isclose(a, b, rel_tol=1e-9), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def comparable(m):
    out = {k: v for k, v in m.items() if k not in RETAINED_ONLY}
    out["drill"] = {k: v for k, v in m["drill"].items() if k not in DRILL_RETAINED_ONLY}
    return out


def test_every_total_reads_the_same_after_old_records_are_purged():
    whole, cut = pair()
    want = aggregate(whole, PRICES)
    got = aggregate(cut, PRICES)
    assert cut.count_rows("heals") < whole.count_rows("heals") and cut.rollup_info()["heals"] > 0       # some really went
    assert want["failures_seen"] > 10 and want["outcomes_by_model"] and want["tests_fixed"] > 0
    same(comparable(want), comparable(got))


def test_the_by_model_rows_survive_the_purge():
    whole, cut = pair()
    want = aggregate(whole, PRICES)["outcomes_by_model"]
    got = aggregate(cut, PRICES)["outcomes_by_model"]
    assert {r["model"] for r in want} == {"small", "big"}
    same(want, got)


def test_a_model_filter_keeps_the_archived_heals_that_used_that_model():
    whole, cut = pair()
    for model in ("small", "big"):
        same(comparable(aggregate(whole, PRICES, model=model)), comparable(aggregate(cut, PRICES, model=model)))


@pytest.mark.parametrize("filters", [{"repo": "alpha"}, {"framework": "fw1"}, {"language": "typescript"}, {"page_language": "fr"},
                                     {"repo": "beta", "framework": "fw2"}])
def test_dimension_filters_apply_to_the_archive_too(filters):
    whole, cut = pair()
    same(comparable(aggregate(whole, PRICES, **filters)), comparable(aggregate(cut, PRICES, **filters)))


def test_a_run_filter_reads_only_retained_records():
    whole, cut = pair()
    mine = aggregate(cut, PRICES, run_id="run1")
    assert mine["failures_seen"] < aggregate(whole, PRICES, run_id="run1")["failures_seen"]
    assert mine["failures_seen"] == len([h for h in cut.heals_in(run_id="run1") if (h["mode"] or "heal") == "heal"])


def test_a_time_window_counts_archived_days_by_day():
    whole, cut = pair()
    since, until = T0 + 3 * DAY, T0 + 20 * DAY               # entirely inside the archived part
    want, got = aggregate(whole, PRICES, since, until), aggregate(cut, PRICES, since, until)
    assert got["failures_seen"] > 0
    assert abs(got["failures_seen"] - want["failures_seen"]) <= 2          # day granularity at the two edges
    day_aligned = (T0 - T0 % DAY) + 3 * DAY, (T0 - T0 % DAY) + 21 * DAY - 1
    same(comparable(aggregate(whole, PRICES, *day_aligned)), comparable(aggregate(cut, PRICES, *day_aligned)))


def test_the_answer_says_what_the_archive_holds_and_what_is_computed_from_retained_records_only():
    _, cut = pair()
    m = aggregate(cut, PRICES)
    assert m["archive"]["heals"] == cut.rollup_info()["heals"] > 0
    assert m["archive"]["through"] is not None and m["archive"]["from"] <= m["archive"]["through"]
    assert set(m["archive"]["retained_only"]) == {"locators_fixed", "hot_spots", "runs", "latency"}
    assert aggregate(Store(":memory:"), PRICES)["archive"]["heals"] == 0


def test_a_purge_between_two_reads_does_not_move_a_total():
    clock = {"t": T0}
    cut = Store(":memory:", 31, lambda: clock["t"])
    populate(cut, clock)
    first = aggregate(cut, PRICES)                  # this read purges
    cut.purge()
    clock["t"] += 3600
    cut.purge()
    same(comparable(first), comparable(aggregate(cut, PRICES)))


# ---- the API and Prometheus --------------------------------------------------------------------------------------------
def app_with(store, token="tok"):
    core = HealSession(ServiceSettings(prices=PRICES), store, FakeModel(), ENDPOINT)
    return core, TestClient(create_app(core, token), base_url="http://127.0.0.1", headers={**JSON, "Authorization": f"Bearer {token}"})


def test_the_metrics_route_totals_do_not_drop_when_records_are_purged():
    whole, cut = pair()
    _, a = app_with(whole)
    _, b = app_with(cut)
    want, got = a.get("/v1/metrics").json(), b.get("/v1/metrics").json()
    assert got["failures_seen"] == want["failures_seen"] and got["healed"] == want["healed"] and got["archive"]["heals"] > 0
    assert b.get("/v1/heals").json()["total"] < a.get("/v1/heals").json()["total"]          # the list holds only what is retained


def test_prometheus_counters_do_not_change_when_records_are_purged():
    clock = {"t": T0}
    store = Store(":memory:", 31, lambda: clock["t"])
    core, c = app_with(store)
    core.telemetry.heal("healed", "heal", "", 0.4, False, 1, ["small"])
    core.telemetry.call("text", "small", True, True, 100, 8, 0, 0.2)
    before = c.get("/metrics").text
    populate(store, clock)
    clock["t"] += 90 * DAY
    store.purge()
    assert store.count_rows("heals") == 0 and store.rollup_info()["heals"] > 0
    assert c.get("/metrics").text == before


def test_no_heal_is_double_counted_when_a_heal_is_followed_up_just_before_it_expires():
    clock = {"t": T0}
    store = Store(":memory:", 31, lambda: clock["t"])
    store.create_heal("h", outcome="healed", drift=1, mode="heal", covers=1)
    store.followup("h", "merged_unchanged", False, 12, 30)
    clock["t"] += 30.9 * DAY
    assert aggregate(store)["durability"]["eligible"] == 1
    clock["t"] += 2 * DAY
    m = aggregate(store)
    assert m["healed"] == 1 and m["durability"]["eligible"] == 1 and m["durability"]["held"] == 1 and m["kept"] == 1
