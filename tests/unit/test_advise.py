"""Advise mode: the decision and the best locator with no patch and no try rounds, under the same gates and budget."""
import json

import pytest
from fastapi.testclient import TestClient

from voyageur.app import create_app
from voyageur.core import HealClosed
from voyageur.metrics import aggregate
from voyageur.schemas import HealRequest, ResultRequest
from tests.unit.scenarios import JSON, BUTTON, PASSWORD, USER, FakeModel, request, session

pytestmark = pytest.mark.unit

PICK_E0 = '{"ref": "e0", "why": "the username box"}'


def advise(**kw):
    body = request(**kw)
    body["options"] = {"mode": "advise"}
    return HealRequest.model_validate(body)


def test_advise_answers_with_the_best_locator_and_no_patch_or_rounds():
    s = session(FakeModel(PICK_E0))
    r = s.start(advise())
    assert r.status == "advised" and r.candidate is None and r.round == 0 and r.ended_at == 5 and r.settled_at == 3
    a = r.advice
    assert a.target_ref == "e0" and a.strategy == "test_id" and a.code == 'self.page.get_by_test_id("login-user")'
    assert a.tier == "testid" and a.tier_movement == "up" and not a.below_floor and a.locator["kind"] == "test_id"
    assert a.alternatives and all(isinstance(x, str) for x in a.alternatives)
    assert "patch" not in json.dumps(r.to_json()).replace("patches", "")      # no patch anywhere in the answer
    assert r.usage.calls == 1


def test_an_advised_heal_is_recorded_with_outcome_advised_and_mode_advise():
    s = session(FakeModel(PICK_E0))
    r = s.start(advise())
    rec = s.get(r.heal_id)
    assert rec["outcome"] == "advised" and rec["mode"] == "advise" and rec["rounds"] == 0 and rec["candidates_tried"] == 0
    assert rec["final_kind"] == "testid" and rec["tier_movement"] == "up" and rec["settled_at"] == 3 and rec["ended_at"] == 5
    assert rec["tries"] == [] and len(rec["calls"]) == 1 and rec["verify_passed"] is None
    assert s.open_conversations() == 0


def test_a_result_sent_to_an_advised_heal_is_refused():
    s = session(FakeModel(PICK_E0))
    r = s.start(advise())
    with pytest.raises(HealClosed):
        s.result(r.heal_id, ResultRequest.model_validate({"count": 1, "hit": {"ref": "e0"}, "test_outcome": "passed"}))


def test_a_fingerprint_match_is_advised_with_no_model_call():
    model = FakeModel()
    last = {"fingerprint": {"tag": "input", "attrs": {"name": "user", "id": "username"}}, "language": "en"}
    r = session(model).start(advise(last_pass=last))
    assert r.status == "advised" and r.settled_at == 2 and r.usage.calls == 0 and model.calls == []


def test_the_same_gates_apply_a_refused_pick_is_not_advised():
    r = session(FakeModel('{"ref": "e9", "why": "not on the list"}')).start(advise())
    assert r.status == "needs_human" and r.advice is None and r.reason == "no_pick"
    none = session(FakeModel('{"ref": null, "none": true}')).start(advise())
    assert none.status == "needs_human" and none.advice is None


def test_not_drift_and_a_missing_target_end_as_they_do_in_heal_mode():
    s = session(FakeModel())
    body = request()
    body["failure"]["error"] = "AssertionError: expected 1 got 2"
    body["options"] = {"mode": "advise"}
    nd = s.start(HealRequest.model_validate(body))
    assert nd.status == "not_drift" and s.get(nd.heal_id)["mode"] == "advise"
    gone = session(FakeModel('{"ref": null, "none": true}')).start(advise(inventory=[PASSWORD, BUTTON]))
    assert gone.status == "needs_human" and gone.reason in ("target_missing", "no_pick")


def test_the_run_token_budget_still_stops_advice():
    s = session(FakeModel(PICK_E0, PICK_E0), budget=100)
    first = s.start(advise())
    assert first.status == "advised"
    second = s.start(advise())
    assert second.status == "needs_human" and second.reason == "budget_exhausted"


def test_advice_never_fills_the_decision_cache_so_a_heal_still_calls_the_model():
    model = FakeModel(PICK_E0, PICK_E0)
    s = session(model)
    assert s.start(advise()).status == "advised"
    heal = s.start(HealRequest.model_validate(request()))
    assert heal.status == "try" and len(model.calls) == 2 and not heal.cache_hit


def test_advise_requests_are_reported_apart_and_do_not_dilute_the_heal_rates():
    s = session(FakeModel(PICK_E0, PICK_E0, PICK_E0))
    other_page = [USER, PASSWORD, BUTTON, {"ref": "e3", "tag": "a", "text": "Help", "attrs": {"id": "help"}}]   # another cache key
    done = s.start(HealRequest.model_validate(request(inventory=other_page)))
    s.result(done.heal_id, ResultRequest.model_validate({"count": 1, "hit": {"ref": "e0"}, "test_outcome": "passed"}))
    s.start(advise())
    s.start(advise())
    m = aggregate(s.store)
    assert m["failures_seen"] == 1 and m["healed"] == 1 and m["success_rate"] == 1.0 and m["model_calls"] == 1
    assert m["advise"]["requests"] == 2 and m["advise"]["advised"] == 2 and m["advise"]["model_calls"] == 2
    assert m["advise"]["input_tokens"] == 200
    assert m["drill"]["advise"] == "mode=advise"
    assert s.store.count_heals(mode="advise") == 2 and s.store.count_heals(mode="heal") == 1


def test_the_api_takes_advise_in_options_and_a_bad_mode_is_a_422():
    core = session(FakeModel(PICK_E0))
    c = TestClient(create_app(core, None), base_url="http://127.0.0.1", headers=JSON)
    body = request()
    body["options"] = {"mode": "advise"}
    r = c.post("/v1/heal", content=json.dumps(body))
    assert r.status_code == 200 and r.json()["status"] == "advised" and r.json()["advice"]["code"]
    assert c.post(f"/v1/heals/{r.json()['heal_id']}/result", content=json.dumps({"count": 1})).status_code == 409
    body["options"] = {"mode": "tell-me-more"}
    assert c.post("/v1/heal", content=json.dumps(body)).status_code == 422


def test_the_default_mode_is_heal():
    assert HealRequest.model_validate(request()).options.mode == "heal"
    assert session(FakeModel(PICK_E0)).start(HealRequest.model_validate(request())).status == "try"
