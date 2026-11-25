"""Comparing models from the record: the `model` filter on GET /v1/metrics, the outcomes-by-model breakdown with a drill link on
every number, the per-model Prometheus counters, and the dashboard's "By model" panel."""
import re
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from voyageur.app import create_app
from voyageur.dashboard import page
from voyageur.metrics import aggregate
from voyageur.schemas import HealRequest, ResultRequest
from voyageur.store import FilterError, Store
from tests.unit.scenarios import JSON, FakeModel, request, session

pytestmark = pytest.mark.unit
PRICES = {"cheap": (1.0, 2.0), "strong": (10.0, 20.0)}
PICK = '{"ref": "e0", "why": "the username box"}'


def filters(qs):
    return {k: v[0] for k, v in parse_qs(qs).items() if k != "sort"}


def seed():
    """Two models over the same kind of failures: `cheap` healed 2 of 4, `strong` 3 of 3; one heal used both (text by cheap,
    vision by strong); one heal made no model call (a fingerprint match); an advise request and a diagnosis, both left out."""
    s = Store(":memory:", 14, lambda: 1000.0)

    def heal(hid, **kw):
        s.create_heal(hid, repo="app", framework="fw", drift=1, mode=kw.pop("mode", "heal"), **kw)

    def call(hid, model, tokens_in=100, tokens_out=10, role="text", step=3):
        s.add_call(hid, role=role, model=model, local=False, input_tokens=tokens_in, output_tokens=tokens_out,
                   images=1 if role == "vision" else 0, latency_ms=10, ok=True, step=step)
    heal("c1", outcome="healed", verify_passed=1, duration_ms=100)
    call("c1", "cheap")
    heal("c2", outcome="healed", verify_passed=0, duration_ms=100)               # kept for a later failure: not verified outright
    call("c2", "cheap", 200, 20)
    heal("c3", outcome="needs_human", reason="no_pick", wrong_element=0, duration_ms=100)
    call("c3", "cheap")
    heal("c4", outcome="needs_human", reason="target_missing", wrong_element=2, duration_ms=100)
    call("c4", "cheap")
    heal("s1", outcome="healed", verify_passed=1, duration_ms=100)
    call("s1", "strong", 300, 30)
    heal("s2", outcome="healed", verify_passed=1, duration_ms=100)
    call("s2", "strong", 300, 30)
    heal("s3", outcome="healed", verify_passed=1, duration_ms=100)
    call("s3", "strong", 300, 30)
    heal("b1", outcome="healed", verify_passed=1, duration_ms=100, wrong_element=1)
    call("b1", "cheap")
    call("b1", "strong", 50, 5, role="vision", step=4)
    heal("f1", outcome="healed", verify_passed=1, duration_ms=5)                  # no model call
    heal("n1", outcome="not_drift", reason="no_locator_in_error", duration_ms=5)
    s.update_heal("n1", drift=0)
    heal("a1", outcome="advised", mode="advise", duration_ms=5)
    call("a1", "cheap")
    heal("d1", outcome="diagnosed", mode="diagnose", duration_ms=5)
    call("d1", "strong")
    return s


def row(m, model):
    return next(r for r in m["outcomes_by_model"] if r["model"] == model)


# ---- the store filter ----------------------------------------------------------------------------------------------
def test_a_heal_matches_the_model_filter_when_any_of_its_calls_used_that_model():
    s = seed()
    cheap = {h["heal_id"] for h in s.list_heals(model="cheap", mode="heal")}
    strong = {h["heal_id"] for h in s.list_heals(model="strong", mode="heal")}
    assert cheap == {"c1", "c2", "c3", "c4", "b1"} and strong == {"s1", "s2", "s3", "b1"}
    assert s.count_heals(model="nobody") == 0


def test_the_model_filter_narrows_the_aggregates_to_the_heals_it_matches():
    s = seed()
    m = aggregate(s, PRICES, model="cheap")
    assert m["failures_seen"] == 5 and m["healed"] == 3 and m["needs_human"] == 2
    assert aggregate(s, PRICES, model="strong")["failures_seen"] == 4
    assert aggregate(s, PRICES, model="nobody")["failures_seen"] == 0
    assert aggregate(s, PRICES)["failures_seen"] == 10


def test_verified_heals_are_a_filter_of_their_own():
    s = seed()
    assert {h["heal_id"] for h in s.list_heals(mode="heal", outcome="healed", verify_passed="1", model="cheap")} == {"c1", "b1"}
    assert s.count_heals(mode="heal", outcome="healed", verify_passed="0") == 1
    with pytest.raises(FilterError):
        s.count_heals(verify_passed="maybe")


# ---- the breakdown -------------------------------------------------------------------------------------------------
def test_outcomes_by_model_counts_each_model_over_the_heals_it_took_part_in():
    m = aggregate(seed(), PRICES)
    assert [r["model"] for r in m["outcomes_by_model"]] == ["cheap", "strong"]
    cheap, strong = row(m, "cheap"), row(m, "strong")
    assert (cheap["heals"], cheap["healed"], cheap["verified_heals"], cheap["needs_human"]) == (5, 3, 2, 2)
    assert cheap["needs_human_by_reason"] == {"no_pick": 1, "target_missing": 1}
    assert (cheap["wrong_element_catches"], cheap["heals_with_wrong_element"]) == (3, 2)
    assert (cheap["model_calls"], cheap["input_tokens"], cheap["output_tokens"]) == (5, 600, 60)
    assert cheap["cost"] == pytest.approx((600 * 1.0 + 60 * 2.0) / 1e6)
    assert cheap["success_rate"] == pytest.approx(3 / 5)
    assert (strong["heals"], strong["healed"], strong["verified_heals"], strong["needs_human"]) == (4, 4, 4, 0)
    assert strong["needs_human_by_reason"] == {} and strong["wrong_element_catches"] == 1
    assert (strong["model_calls"], strong["input_tokens"], strong["output_tokens"]) == (4, 950, 95)
    assert strong["cost"] == pytest.approx((950 * 10.0 + 95 * 20.0) / 1e6)


def test_a_heal_that_used_two_models_is_counted_under_both_and_the_note_says_so():
    m = aggregate(seed(), PRICES)
    assert row(m, "cheap")["heals"] + row(m, "strong")["heals"] == 9      # 8 single-model heals + b1 twice
    assert m["outcomes_by_model_note"]


def test_heals_with_no_model_call_are_listed_apart_and_advise_and_diagnose_are_left_out():
    m = aggregate(seed(), PRICES)
    assert m["outcomes_without_model"] == {"heals": 2, "healed": 1, "needs_human": 0}      # f1 (healed) and n1 (not drift)
    assert sum(r["model_calls"] for r in m["outcomes_by_model"]) == m["model_calls"]


def test_cost_is_none_for_a_model_without_a_price():
    m = aggregate(seed(), {"strong": (10.0, 20.0)})
    assert row(m, "cheap")["cost"] is None and row(m, "strong")["cost"] is not None


def test_an_empty_store_has_an_empty_breakdown():
    m = aggregate(Store(":memory:", 14))
    assert m["outcomes_by_model"] == [] and m["outcomes_without_model"] == {"heals": 0, "healed": 0, "needs_human": 0}


def test_the_model_filter_gives_the_breakdown_of_that_model_alone_so_every_number_still_matches_its_link():
    s = seed()
    m = aggregate(s, PRICES, model="strong")
    assert [r["model"] for r in m["outcomes_by_model"]] == ["strong"]             # b1 also used cheap: that row would count b1 only
    assert row(m, "strong")["heals"] == 4 and list(m["drill"]["by_model"]) == ["strong"]
    assert s.count_heals(**filters(m["drill"]["by_model"]["strong"]["heals"])) == 4


# ---- every number drills -------------------------------------------------------------------------------------------
def test_every_number_in_the_breakdown_has_a_drill_link_that_lists_exactly_its_heals():
    s = seed()
    m = aggregate(s, PRICES)
    for model in ("cheap", "strong"):
        r, d = row(m, model), m["drill"]["by_model"][model]
        assert s.count_heals(**filters(d["heals"])) == r["heals"]
        assert s.count_heals(**filters(d["healed"])) == r["healed"]
        assert s.count_heals(**filters(d["verified_heals"])) == r["verified_heals"]
        assert s.count_heals(**filters(d["needs_human"])) == r["needs_human"]
        assert s.count_heals(**filters(d["wrong_element_catches"])) == r["heals_with_wrong_element"]
        assert s.count_heals(**filters(d["model_calls"])) == r["heals"]
        for reason, n in r["needs_human_by_reason"].items():
            assert s.count_heals(**filters(d["needs_human_by_reason"][reason])) == n
    assert s.count_heals(**filters(m["drill"]["outcomes_without_model"])) == m["outcomes_without_model"]["heals"]


def test_a_model_name_with_odd_characters_is_encoded_in_its_drill_link():
    s = Store(":memory:", 14, lambda: 1.0)
    s.create_heal("x", drift=1, mode="heal", outcome="healed")
    s.add_call("x", role="text", model="org/model:7b&x=1", local=False, input_tokens=1, output_tokens=1, images=0,
               latency_ms=1, ok=True, step=3)
    qs = aggregate(s)["drill"]["by_model"]["org/model:7b&x=1"]["heals"]
    assert filters(qs)["model"] == "org/model:7b&x=1" and s.count_heals(**filters(qs)) == 1


# ---- the API -------------------------------------------------------------------------------------------------------
def api():
    core = session(FakeModel(PICK))
    core.store = seed()
    return TestClient(create_app(core, "tok"), base_url="http://127.0.0.1", headers={**JSON, "Authorization": "Bearer tok"})


def test_get_metrics_takes_a_model_filter_and_returns_the_breakdown():
    c = api()
    whole = c.get("/v1/metrics").json()
    cheap = c.get("/v1/metrics", params={"model": "cheap"}).json()
    assert whole["failures_seen"] == 10 and cheap["failures_seen"] == 5
    assert {r["model"] for r in whole["outcomes_by_model"]} == {"cheap", "strong"}
    assert c.get("/v1/metrics", params={"model": "nobody"}).json()["failures_seen"] == 0


def test_get_heals_lists_the_heals_behind_a_by_model_number():
    c = api()
    qs = c.get("/v1/metrics").json()["drill"]["by_model"]["cheap"]["needs_human"]
    body = c.get("/v1/heals?" + qs).json()
    assert body["total"] == 2 and {h["heal_id"] for h in body["heals"]} == {"c3", "c4"}
    assert c.get("/v1/heals", params={"model": "strong", "mode": "heal", "verify_passed": "1"}).json()["total"] == 4
    assert c.get("/v1/heals", params={"verify_passed": "maybe"}).status_code == 422


# ---- Prometheus ----------------------------------------------------------------------------------------------------
def sample(text, name, **labels):
    for line in text.splitlines():
        m = re.fullmatch(rf"{name}(?:\{{(.*)\}})? (\S+)", line)
        if m and dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', m.group(1) or "")) == labels:
            return float(m.group(2))
    return None


def test_prometheus_counts_outcomes_per_model():
    core = session(FakeModel(PICK))
    r = core.start(HealRequest.model_validate(request()))
    core.result(r.heal_id, ResultRequest.model_validate({"count": 1, "hit": {"ref": "e0"}, "test_outcome": "passed"}))
    text = core.telemetry.render()
    assert sample(text, "voyageur_model_heals_total", model="test-model", outcome="healed") == 1
    assert sample(text, "voyageur_model_heals_total", model="other", outcome="healed") is None
    assert "# TYPE voyageur_model_heals_total counter" in text


def test_prometheus_counts_needs_human_reasons_and_wrong_elements_per_model_and_leaves_out_advise():
    core = session(FakeModel('{"ref": null, "none": true}'))
    core.start(HealRequest.model_validate(request()))
    advise = request()
    advise["options"] = {"mode": "advise"}
    core.start(HealRequest.model_validate(advise))
    text = core.telemetry.render()
    assert sample(text, "voyageur_model_needs_human_total", model="test-model", reason="no_pick") == 1
    assert sample(text, "voyageur_model_heals_total", model="test-model", outcome="needs_human") == 1
    assert "voyageur_model_wrong_element_total" in text          # declared, even with nothing to count yet


def test_a_heal_with_no_model_call_adds_no_per_model_series():
    from voyageur.telemetry import Telemetry
    t = Telemetry()
    t.heal("healed", "heal", "", 0.1, False, 0)
    assert "voyageur_model_heals_total{" not in t.render()


# ---- the dashboard -------------------------------------------------------------------------------------------------
def test_the_dashboard_has_a_by_model_panel_that_reads_the_breakdown_and_links_every_number():
    html, _ = page()
    assert "By model" in html and "outcomes_by_model" in html
    for key in ("healed", "verified_heals", "needs_human", "wrong_element_catches", "model_calls"):
        assert f"D.by_model[r.model].{key}" in html, key          # every number is a link into the heal list
    assert "outcomes_without_model" in html and "needs_human_by_reason" in html and "D.outcomes_without_model" in html


def test_the_by_model_panel_keeps_the_page_policy():
    html, _ = page()
    assert not re.search(r"https?://", html) and "url(" not in html and "@import" not in html and "<link" not in html
    assert "innerHTML" not in html
