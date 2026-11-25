"""Every aggregate drills down: the filter string in `drill` lists exactly the heals behind the number, the post-PR signals
feed kept rate, durability and escaped wrong heals, and the CSV export defuses spreadsheet formulas."""
import csv
import io
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from voyageur.app import create_app
from voyageur.core import NotHealed, UnknownHeal
from voyageur.export import COLUMNS, csv_cell, heals_csv
from voyageur.metrics import aggregate
from voyageur.schemas import FollowupRequest, HealRequest, ResultRequest
from voyageur.store import FilterError, Store
from tests.unit.scenarios import JSON, FakeModel, request, session

pytestmark = pytest.mark.unit
NOW = 1_800_000_000.0


def filters(qs):
    """The store filters a drill query string stands for."""
    return {k: v[0] for k, v in parse_qs(qs).items() if k != "sort"}


def seed():
    clock = [NOW]
    s = Store(":memory:", 14, lambda: clock[0])

    def heal(hid, **kw):
        clock[0] += 60
        s.create_heal(hid, repo="app", framework="fw", language="python", run_id=kw.pop("run", "r1"), mode=kw.pop("mode", "heal"), **kw)

    def call(hid, tokens_in=100, tokens_out=10, local=True, model="m", role="text", ms=10, step=3):
        s.add_call(hid, role=role, model=model, local=local, input_tokens=tokens_in, output_tokens=tokens_out, images=0,
                   latency_ms=ms, ok=True, step=step)
    heal("a", drift=1, outcome="healed", settled_at=3, ended_at=7, covers=3, locator_key="p.py|x", file="p.py", tier_movement="up",
         page_language="fr", branch=1, wrong_element=1, duration_ms=100, pr_state="merged_unchanged", failed_again=0,
         runs_since=12, days_since=40, followup_at=NOW)
    call("a")
    call("a", role="vision", model="v", local=False, step=4)
    heal("b", drift=1, outcome="healed", settled_at=2, ended_at=7, covers=1, locator_key="p.py|y", file="p.py", tier_movement="same",
         page_language="en", duration_ms=10, pr_state="edited", failed_again=0, runs_since=3, days_since=2, followup_at=NOW)
    heal("c", drift=1, outcome="needs_human", reason="budget_exhausted", ended_at=3, budget_exhausted=1, duration_ms=30, run="r2")
    call("c", local=False, model="big")
    heal("d", drift=1, outcome="needs_human", reason="no_pick", ended_at=3, duration_ms=50, run="r2")
    heal("e", drift=0, outcome="not_drift", reason="no_locator_in_error", ended_at=1, duration_ms=1, run="r2")
    heal("f", drift=1, outcome="try", duration_ms=0, run="r2")
    heal("g", drift=1, outcome="healed", settled_at=3, ended_at=7, covers=1, locator_key="p.py|z", file="p.py", tier_movement="down",
         page_language="fr", duration_ms=40, pr_state="reverted", failed_again=1, runs_since=2, days_since=1, followup_at=NOW,
         broke_again_after=2, cache_hit=1)
    call("g", model="big", local=False)
    heal("h", drift=1, outcome="healed", settled_at=2, ended_at=7, covers=1, locator_key="p.py|q", file="p.py", tier_movement="up",
         duration_ms=20, pr_state="merged_unchanged", failed_again=1, runs_since=15, days_since=45, followup_at=NOW, broke_again_after=15)
    heal("i", drift=1, outcome="healed", settled_at=3, ended_at=7, covers=1, locator_key="p.py|r", file="p.py", tier_movement="up",
         duration_ms=21, pr_state="closed_unmerged")
    call("i")
    heal("j", drift=1, outcome="advised", ended_at=5, mode="advise", duration_ms=5)
    call("j")
    return s


def test_post_pr_signals_drive_kept_rate_durability_and_escaped_wrong_heals():
    m = aggregate(seed())
    assert (m["healed"], m["kept"], m["pr_closed"]) == (5, 2, 5) and m["kept_rate"] == pytest.approx(2 / 5)
    assert m["escaped_wrong_heals"] == 2 and m["pr_state"]["edited"] == 1 and m["pr_state"]["reverted"] == 1
    d = m["durability"]
    assert (d["eligible"], d["held"], d["broke_again"]) == (2, 1, 2) and d["rate"] == pytest.approx(1 / 2)
    assert d["pending"] == 1 and m["broke_again"] == 2        # b has a follow-up but is too young; g and h broke again


def test_every_count_drills_to_the_heals_behind_it():
    s = seed()
    m = aggregate(s)
    d = m["drill"]

    def n(key):
        return s.count_heals(**filters(d[key]))
    assert n("failures_seen") == m["failures_seen"] == 9
    assert n("locator_drift") == m["locator_drift"] and n("not_drift") == m["not_drift"]
    assert n("healed") == m["healed"] and n("needs_human") == m["needs_human"] and n("open") == m["open"]
    assert n("pr_closed") == m["pr_closed"] and n("escaped_wrong_heals") == m["escaped_wrong_heals"]
    assert n("kept_rate") == m["kept"]
    assert n("wrong_element_catches") == m["heals_with_wrong_element"]
    assert n("budget_exhausted") == m["budget_exhausted"] and n("cache") == m["cache"]["hits"]
    assert n("branches_added") == m["branches_added"]
    assert n("durability") == m["durability"]["held"] and n("durability_failed") == m["durability"]["broke_again"]
    assert n("durability_pending") == m["durability"]["pending"]
    assert n("advise") == m["advise"]["requests"] == 1
    # tests fixed is the sum of covers over the heals listed
    listed = s.list_heals(**filters(d["tests_fixed"]))
    assert sum(h["covers"] or 1 for h in listed) == m["tests_fixed"]
    assert len({h["locator_key"] for h in listed}) == m["locators_fixed"]
    # calls: the calls of the heals listed are the number
    def calls_of(qs):
        return sum(len(s.get_heal(h["heal_id"])["calls"]) for h in s.list_heals(**filters(qs)))
    assert calls_of(d["model_calls"]) == m["model_calls"] == 5
    assert calls_of(d["calls_that_led_to_fix"]) == m["calls_that_led_to_fix"]


def test_every_breakdown_entry_drills_to_its_own_count():
    s = seed()
    m = aggregate(s)
    d = m["drill"]
    for reason, n in m["needs_human_by_reason"].items():
        assert s.count_heals(**filters(d["needs_human_by_reason"][reason])) == n
    for outcome, n in m["outcome_mix"].items():
        assert s.count_heals(**filters(d["outcome_mix"][outcome])) == n
    for step, n in m["settled_at"].items():
        assert s.count_heals(**filters(d["settled_at"][step])) == n
    for move, n in m["tier_movement"].items():
        assert s.count_heals(**filters(d["tier_movement"][move])) == n
    for lang, n in m["language"].items():
        assert s.count_heals(**filters(d["language"][lang])) == n
    for state, n in m["pr_state"].items():
        assert s.count_heals(**filters(d["pr_state"][state])) == n
    for run in m["runs"]:
        assert s.count_heals(**filters(d["runs"][run["run_id"]])) == run["heals"]
    for spot in m["hot_spots"]:
        assert s.count_heals(**filters(d["hot_spots"][spot["file"] + "|" + spot["locator_key"]])) == spot["heals"]
    for row in m["cost_by_model"]:
        assert s.count_heals(**filters(d["cost_by_model"][row["role"] + "|" + row["model"]])) >= 1
    assert s.count_heals(**filters(d["cost_cloud"])) == 3 and s.count_heals(**filters(d["vision"])) == 1


def test_cost_latency_and_runs_cover_the_spec_detail_table():
    m = aggregate(seed(), prices={"m": (1.0, 2.0), "big": (10.0, 20.0), "v": (3.0, 3.0)})
    assert m["cost"] is not None and m["cost_per_fix"] == pytest.approx(m["cost"] / m["tests_fixed"])
    assert {r["role"] for r in m["cost_by_role"]} == {"text", "vision"} and all(r["cost"] is not None for r in m["cost_by_role"])
    assert 0 < m["cloud_token_share"] < 1 and m["local_token_share"] + m["cloud_token_share"] == pytest.approx(1)
    assert m["vision"] == {"calls": 1, "heals": 1} and m["tokens_per_kept_heal"] == pytest.approx(
        (m["input_tokens"] + m["output_tokens"]) / m["kept"])
    assert m["latency"]["heal_ms_max"] == 100 and m["latency"]["model_ms_by_step"]["3"] == 10
    assert {r["run_id"]: (r["healed"], r["needs_human"], r["not_drift"], r["open"]) for r in m["runs"]} == {
        "r1": (5, 0, 0, 0), "r2": (0, 2, 1, 1)}
    assert m["client_language"] == {"python": 9} and m["framework"] == {"fw": 9}


def test_filters_that_take_several_values_flags_sorting_and_bad_values():
    s = seed()
    assert {h["heal_id"] for h in s.list_heals(pr_state="edited,reverted")} == {"b", "g"}
    assert {h["heal_id"] for h in s.list_heals(reason="unknown", outcome="healed")} == {"a", "b", "g", "h", "i"}
    assert [h["heal_id"] for h in s.list_heals(sort="slowest", limit=2)] == ["a", "d"]
    assert {h["heal_id"] for h in s.list_heals(model="big")} == {"c", "g"} and {h["heal_id"] for h in s.list_heals(local=0)} == {"a", "c", "g"}
    assert s.count_heals(wrong_element=0, mode="heal") == 8
    with pytest.raises(FilterError):
        s.list_heals(cache_hit="maybe")
    with pytest.raises(FilterError):
        s.list_heals(sort="oldest")
    with pytest.raises(FilterError):
        s.list_heals(durability="sometimes")


def test_a_filter_value_is_a_parameter_never_sql():
    s = seed()
    assert s.list_heals(outcome="healed' OR '1'='1") == [] and s.count_heals(file="p.py'; DROP TABLE heals; --") == 0
    assert s.count_heals() == 10


# ---- the follow-up through the core and the API ----------------------------------------------------------------------
PICK = '{"ref": "e0", "why": "the username box"}'
OK = {"count": 1, "hit": {"ref": "e0"}, "test_outcome": "passed"}


def healed(core):
    r = core.start(HealRequest.model_validate(request()))
    core.result(r.heal_id, ResultRequest.model_validate(OK))
    return r.heal_id


def test_a_followup_is_stored_on_a_healed_heal_and_counted():
    core = session(FakeModel(PICK))
    hid = healed(core)
    out = core.followup(hid, FollowupRequest(repo="app", pr_state="merged_unchanged", failed_again=False, runs_since=12, days_since=31))
    assert out["pr_state"] == "merged_unchanged"
    rec = core.get(hid)
    assert (rec["pr_state"], rec["failed_again"], rec["runs_since"], rec["days_since"]) == ("merged_unchanged", 0, 12, 31)
    assert rec["followup_at"] is not None and rec["broke_again_after"] is None
    m = aggregate(core.store)
    assert m["kept_rate"] == 1.0 and m["durability"]["rate"] == 1.0
    core.followup(hid, FollowupRequest(repo="app", pr_state="reverted", failed_again=True, runs_since=4, days_since=5))
    rec = core.get(hid)
    assert rec["pr_state"] == "reverted" and rec["broke_again_after"] == 4
    m = aggregate(core.store)
    assert m["escaped_wrong_heals"] == 1 and m["kept_rate"] == 0.0 and m["broke_again"] == 1
    assert 'followups_total{pr_state="reverted"} 1' in core.telemetry.render()


def test_a_followup_for_an_unknown_or_unhealed_heal_is_refused():
    core = session(FakeModel('{"ref": null, "none": true}'))
    r = core.start(HealRequest.model_validate(request()))
    with pytest.raises(NotHealed):
        core.followup(r.heal_id, FollowupRequest(repo="app", pr_state="open"))
    with pytest.raises(UnknownHeal):
        core.followup("h_nope", FollowupRequest(repo="app", pr_state="open"))


def test_the_followup_route_and_its_validation():
    core = session(FakeModel(PICK))
    c = TestClient(create_app(core, None), base_url="http://127.0.0.1", headers=JSON)
    hid = healed(core)
    ok = c.post(f"/v1/heals/{hid}/followup", json={"repo": "app", "pr_state": "merged_unchanged", "failed_again": False, "runs_since": 10, "days_since": 30})
    assert ok.status_code == 200 and ok.json()["runs_since"] == 10
    assert c.post(f"/v1/heals/{hid}/followup", json={"repo": "app", "pr_state": "exploded"}).status_code == 422
    assert c.post(f"/v1/heals/{hid}/followup", json={"repo": "app", "pr_state": "open", "runs_since": -1}).status_code == 422
    assert c.post(f"/v1/heals/{hid}/followup", json={"repo": "app", "pr_state": "open", "surprise": 1}).status_code == 422
    assert c.post("/v1/heals/h_nope/followup", json={"repo": "app", "pr_state": "open"}).status_code == 404
    assert c.get("/v1/heals?pr_state=merged_unchanged&durability=pass").json()["total"] == 1
    other = core.start(HealRequest.model_validate(request(inventory=[])))
    assert c.post(f"/v1/heals/{other.heal_id}/followup", json={"repo": "app", "pr_state": "open"}).status_code == 409


def test_the_token_guards_the_new_routes():
    c = TestClient(create_app(session(FakeModel()), "tok"), base_url="http://127.0.0.1", headers=JSON)
    for method, path in (("post", "/v1/heals/h_x/followup"), ("post", "/v1/audit"), ("get", "/metrics")):
        assert getattr(c, method)(path).status_code == 401, path


# ---- the list route and the CSV export ---------------------------------------------------------------------------------
def api():
    core = session(FakeModel())
    core.store = seed()
    return TestClient(create_app(core, None), base_url="http://127.0.0.1", headers=JSON)


def test_the_list_route_takes_every_filter_and_reports_the_total():
    c = api()
    body = c.get("/v1/heals?outcome=healed&pr_state=edited,reverted&limit=1").json()
    assert body["total"] == 2 and body["count"] == 1 and body["heals"][0]["links"]["self"].startswith("/v1/heals/")
    assert c.get("/v1/heals?run=r2").json()["total"] == 4 and c.get("/v1/heals?run_id=r2").json()["total"] == 4
    assert c.get("/v1/heals?settled_at=3&outcome=healed").json()["total"] == 3
    assert c.get("/v1/heals?tier_movement=up&repo=app&framework=fw").json()["total"] == 3
    assert c.get("/v1/heals?from=2027-01-01&to=2030-01-01").json()["total"] == 10
    assert c.get("/v1/heals?cache_hit=maybe").status_code == 422
    assert c.get("/v1/heals?sort=oldest").status_code == 422 and c.get("/v1/heals?format=xml").status_code == 422
    assert c.get("/v1/heals?limit=5000").status_code == 422 and c.get("/v1/heals?format=csv&limit=5000").status_code == 200


def test_the_csv_export_has_the_same_rows_and_defuses_formulas():
    c = api()
    r = c.get("/v1/heals?format=csv&outcome=healed")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert tuple(rows[0]) == COLUMNS and len(rows) - 1 == c.get("/v1/heals?outcome=healed").json()["total"]
    first = dict(zip(rows[0], rows[1]))
    assert first["created_at"].endswith("Z") and first["mode"] == "heal"


@pytest.mark.parametrize("text", ["=1+1", "+SUM(A1)", "-2+3", "@cmd", "\tx", "\rx", " =HYPERLINK(\"x\")"])
def test_a_cell_starting_with_a_formula_character_is_quoted(text):
    assert csv_cell(text) == "'" + text


def test_other_cells_are_left_alone_and_numbers_stay_numbers():
    assert csv_cell("a=b") == "a=b" and csv_cell(None) == "" and csv_cell(7) == 7 and csv_cell(-7) == -7 and csv_cell(True) == 1


def test_a_hostile_locator_cannot_start_a_formula_in_the_export():
    row = {"heal_id": "h_1", "created_at": NOW, "broken_locator": "=cmd|' /C calc'!A0", "test_id": "@SUM(1)", "why": "-1+1"}
    out = list(csv.reader(io.StringIO(heals_csv([row]))))
    cells = dict(zip(out[0], out[1]))
    assert cells["broken_locator"].startswith("'=") and cells["test_id"].startswith("'@") and cells["why"].startswith("'-")
