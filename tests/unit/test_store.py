"""Store: the SQLite heal record, model calls, tries, decision cache and retention."""
import pytest

from voyageur.store import Store

pytestmark = pytest.mark.unit


def new(retention=14, now=1000.0):
    clock = {"t": now}
    return Store(":memory:", retention, lambda: clock["t"]), clock


def test_a_heal_round_trips_with_its_calls_and_tries():
    s, _ = new()
    s.create_heal("h1", run_id="r1", repo="app", framework="fw", language="python", test_id="t::a", drift=1)
    s.update_heal("h1", outcome="try", rounds=1, broken_kind="id")
    s.add_call("h1", role="text", model="m", local=True, input_tokens=100, output_tokens=5, images=0, latency_ms=12,
               ok=True, step=3)
    s.add_try("h1", 1, "e7", "testid", "page.get_by_test_id('x')", "pending")
    s.update_try("h1", 1, "wrong_element", "hit e9")
    rec = s.get_heal("h1")
    assert rec["run_id"] == "r1" and rec["outcome"] == "try" and rec["calls"][0]["input_tokens"] == 100
    assert rec["tries"][0]["result"] == "wrong_element" and rec["tries"][0]["detail"] == "hit e9"
    assert s.get_heal("nope") is None


def test_update_refuses_a_column_that_does_not_exist():
    s, _ = new()
    s.create_heal("h1")
    with pytest.raises(ValueError):
        s.update_heal("h1", nonsense=1)


def test_run_tokens_sum_input_and_output_of_a_run_only():
    s, _ = new()
    for hid, run in (("a", "r1"), ("b", "r1"), ("c", "r2")):
        s.create_heal(hid, run_id=run)
        s.add_call(hid, role="text", model="m", local=True, input_tokens=10, output_tokens=2, images=0, latency_ms=1,
                   ok=True, step=3)
    assert s.run_tokens("r1") == 24 and s.run_tokens("r2") == 12 and s.run_tokens("zz") == 0


def test_the_decision_cache_round_trips_and_expires_with_retention():
    s, clock = new(retention=1)
    s.cache_put("k", {"ref": "e1"})
    assert s.cache_get("k") == {"ref": "e1"} and s.cache_get("other") is None
    clock["t"] += 2 * 86400
    assert s.cache_get("k") is None


def test_purge_deletes_records_older_than_retention_with_their_children():
    s, clock = new(retention=1)
    s.create_heal("old")
    s.add_call("old", role="text", model="m", local=True, input_tokens=1, output_tokens=1, images=0, latency_ms=1, ok=True, step=3)
    clock["t"] += 2 * 86400
    s.create_heal("new")
    s.purge()
    assert s.get_heal("old") is None and s.get_heal("new") is not None
    assert s.count_rows("calls") == 0


def test_retention_zero_keeps_nothing_once_a_heal_is_finished_and_caches_nothing():
    s, _ = new(retention=0)
    s.create_heal("h1")
    assert s.get_heal("h1") is not None             # the conversation is open: needed to answer its result
    s.add_call("h1", role="text", model="m", local=True, input_tokens=1, output_tokens=1, images=0, latency_ms=1, ok=True, step=3)
    s.finish("h1")
    assert s.get_heal("h1") is None and s.count_rows("calls") == 0
    s.cache_put("k", {"a": 1})
    assert s.cache_get("k") is None


def test_list_filters_and_pages():
    s, _ = new()
    for i, (outcome, fw) in enumerate([("healed", "a"), ("needs_human", "a"), ("healed", "b")]):
        s.create_heal(f"h{i}", framework=fw, outcome=outcome, repo="r")
    assert [r["heal_id"] for r in s.list_heals(outcome="healed")] == ["h2", "h0"]
    assert [r["heal_id"] for r in s.list_heals(framework="a")] == ["h1", "h0"]
    assert len(s.list_heals(limit=1)) == 1 and s.list_heals(offset=5) == []
    assert s.list_heals(repo="nobody") == []
