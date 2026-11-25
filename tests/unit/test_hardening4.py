"""Phase 4 hardening: audit work budget and slots, follow-up ownership and idempotence, Host and Content-Type checks on an open
service, bounded filters, CSV whitespace and paging, MCP total size, and advice that says it is unchecked."""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from voyageur.app import MAX_INT, create_app
from voyageur.audit import MAX_AUDIT_EXPR, run_audit
from voyageur.config import ServiceSettings
from voyageur.core import Busy, HealSession
from voyageur.deadline import InlineRunner
from voyageur.export import COLUMNS, csv_cell, csv_chunks
from voyageur.mcp_server import ToolRefused, audit_locators
from voyageur.schemas import HealRequest, LimitExceeded, ResultRequest, parse_audit_request, parse_request
from voyageur.store import Store
from tests.unit.scenarios import ENDPOINT, JSON, FakeModel, request, session
from tests.unit.test_audit import body as audit_body

pytestmark = pytest.mark.unit
PICK = '{"ref": "e0", "why": "the username box"}'
OK = ResultRequest.model_validate({"count": 1, "hit": {"ref": "e0"}, "test_outcome": "passed"})


def client(core=None, token=None, **kw):
    return TestClient(create_app(core or session(FakeModel(PICK)), token, **kw), base_url=kw.pop("base", "http://127.0.0.1"),
                      headers=JSON)


# ---- M1: the audit has its own budget and slots -----------------------------------------------------------------------------
def slow_body(n=200):
    line = "        self.page.get_by_role('button', name='Save').click()\n"
    els = [{"ref": f"e{i}", "tag": "button", "role": "button", "name": "Save", "text": "Save", "box": {"x": 1, "y": i, "w": 10, "h": 10}}
           for i in range(300)]
    return {"client": {"framework": "x", "language": "python"},
            "source": {"roots": ["p"], "files": [{"path": "p/a.py", "content": line * n}]},
            "page": {"url": "http://a/b", "inventory": els}}


def test_an_audit_past_its_budget_answers_with_the_rows_done_and_truncated_not_an_error():
    out = run_audit(parse_audit_request(slow_body(300)), budget_s=0.0)
    assert out["truncated"] is True and len(out["locators"]) <= 1
    full = run_audit(parse_audit_request(slow_body(5)), budget_s=60)
    assert full["truncated"] is False and len(full["locators"]) == 5


def test_the_route_returns_200_truncated_when_the_budget_is_spent():
    core = HealSession(ServiceSettings(audit_budget_s=0.1), Store(":memory:", 14), FakeModel(), ENDPOINT, runner=InlineRunner())
    r = client(core).post("/v1/audit", content=json.dumps(slow_body(500)))
    assert r.status_code == 200 and r.json()["truncated"] is True and r.json()["summary"]["locators"] == len(r.json()["locators"])


def test_a_stopped_worker_gives_no_rows_and_truncated_not_a_503():
    class Dying:
        def run(self, fn, *a, timeout=None):
            from voyageur.deadline import DeadlineExceeded
            raise DeadlineExceeded("stopped")
    core = HealSession(ServiceSettings(), Store(":memory:", 14), FakeModel(), ENDPOINT, runner=Dying())
    r = client(core).post("/v1/audit", content=json.dumps(audit_body()))
    assert r.status_code == 200 and r.json()["truncated"] is True and r.json()["locators"] == []


def test_a_call_over_the_audit_expression_cap_is_unknown_and_not_evaluated():
    long_sel = "button" + "[class*=btn]" * 120
    assert len(long_sel) > MAX_AUDIT_EXPR
    body = slow_body(1)
    body["source"]["files"][0]["content"] = f"        self.page.locator('{long_sel}').click()\n"
    row = run_audit(parse_audit_request(body))["locators"][0]
    assert row["status"] == "unknown" and "characters" in row["why"] and row["matches"] is None


def test_at_most_workers_minus_one_audits_run_at_once_and_a_heal_still_gets_a_worker():
    core = HealSession(ServiceSettings(workers=2), Store(":memory:", 14), FakeModel(PICK), ENDPOINT, runner=InlineRunner())
    release, entered = threading.Event(), threading.Event()

    class Hold(InlineRunner):
        def run(self, fn, *a, timeout=None):
            if fn is run_audit:
                entered.set()
                release.wait(5)
            return super().run(fn, *a, timeout=timeout)
    core.runner = Hold()
    c = client(core)
    first = []
    t = threading.Thread(target=lambda: first.append(c.post("/v1/audit", content=json.dumps(audit_body())).status_code))
    t.start()
    assert entered.wait(5)
    r = c.post("/v1/audit", content=json.dumps(audit_body()))
    assert r.status_code == 429 and r.headers["retry-after"]
    heal = c.post("/v1/heal", content=json.dumps(request()))
    assert heal.status_code == 200 and heal.json()["status"] == "try"          # the held audit does not starve a heal
    release.set()
    t.join(5)
    assert first == [200]
    assert c.post("/v1/audit", content=json.dumps(audit_body())).status_code == 200      # the slot came back


def test_the_mcp_audit_takes_the_same_slot_path():
    core = HealSession(ServiceSettings(workers=2), Store(":memory:", 14), FakeModel(), ENDPOINT, runner=InlineRunner())
    for _ in range(1):
        assert core._audit_slots.acquire(blocking=False)
    with pytest.raises(ToolRefused, match="too many audits"):
        audit_locators(core, audit_body())
    with pytest.raises(Busy):
        core.audit(parse_audit_request(audit_body()))


# ---- M2: a follow-up belongs to its record ------------------------------------------------------------------------------------
def healed_core():
    core = session(FakeModel(PICK))
    r = core.start(HealRequest.model_validate(request()))
    core.result(r.heal_id, OK)
    return core, r.heal_id


def follow(c, hid, **over):
    return c.post(f"/v1/heals/{hid}/followup", json={"repo": "app", "pr_state": "open", **over})


def test_a_followup_must_name_the_records_repo_and_run():
    core, hid = healed_core()
    c = client(core)
    assert c.post(f"/v1/heals/{hid}/followup", json={"pr_state": "open"}).status_code == 422          # repo is required
    assert follow(c, hid, repo="other").status_code == 409
    assert follow(c, hid, repo="").status_code == 409                       # the record has a repo
    assert follow(c, hid, run_id="elsewhere").status_code == 409
    assert follow(c, hid, run_id="run1").status_code == 200 and follow(c, hid).status_code == 200
    assert core.get(hid)["pr_state"] == "open"


def test_a_record_with_no_repo_takes_only_an_empty_repo():
    core = session(FakeModel(PICK))
    body = request()
    body["run"] = {"id": "run1"}
    r = core.start(HealRequest.model_validate(body))
    core.result(r.heal_id, OK)
    c = client(core)
    assert follow(c, r.heal_id, repo="app").status_code == 409
    assert follow(c, r.heal_id, repo="").status_code == 200


def test_failed_again_stays_true_and_the_state_change_is_counted_once():
    core, hid = healed_core()
    c = client(core)
    follow(c, hid, pr_state="merged_unchanged", failed_again=True, runs_since=4)
    for _ in range(3):
        follow(c, hid, pr_state="merged_unchanged", failed_again=False, runs_since=9)
    rec = core.get(hid)
    assert rec["failed_again"] == 1 and rec["broke_again_after"] == 4 and rec["runs_since"] == 9
    text = core.telemetry.render()
    assert 'voyageur_followups_total{pr_state="merged_unchanged"} 1' in text
    assert "voyageur_followup_failed_again_total 1" in text
    follow(c, hid, pr_state="reverted", failed_again=False)
    assert 'voyageur_followups_total{pr_state="reverted"} 1' in core.telemetry.render()
    assert follow(c, hid, pr_state="reverted").json()["failed_again"] is True


# ---- L5: an open service answers only to loopback, and every POST says JSON -----------------------------------------------------
@pytest.mark.parametrize("host,ok", [("127.0.0.1", True), ("127.0.0.1:8765", True), ("localhost:9", True), ("[::1]:8765", True),
                                     ("LOCALHOST", True), ("rebind.evil.example", False), ("rebind.evil.example:8765", False),
                                     ("127.0.0.1.evil.example", False), ("", False), ("testserver", False)])
def test_an_open_service_refuses_a_host_that_is_not_loopback(host, ok):
    c = client()
    r = c.get("/healthz", headers={"Host": host})
    assert (r.status_code == 200) is ok, (host, r.status_code)


def test_the_bind_address_is_allowed_and_a_token_means_no_host_check():
    assert client(bind_host="10.0.0.5").get("/healthz", headers={"Host": "10.0.0.5:8765"}).status_code == 200
    assert client(bind_host="10.0.0.5").get("/healthz", headers={"Host": "other.example"}).status_code == 403
    assert client(token="tok").get("/healthz", headers={"Host": "svc.example"}).status_code == 200


def test_every_post_route_needs_a_json_content_type():
    c = TestClient(create_app(session(FakeModel(PICK)), None), base_url="http://127.0.0.1")
    for path in ("/v1/heal", "/v1/audit", "/v1/heals/h_x/result", "/v1/heals/h_x/followup"):
        for ctype in (None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            r = c.post(path, content=b"{}", headers={"Content-Type": ctype} if ctype else {})
            assert r.status_code == 415, (path, ctype, r.status_code)
        assert c.post(path, content=b"{}", headers={"Content-Type": "application/json; charset=utf-8"}).status_code != 415


# ---- L6: bounded filters, CSV whitespace and paging ---------------------------------------------------------------------------
def test_numeric_filters_are_bounded_and_list_filters_capped():
    c = client(healed_core()[0])
    for q in ("settled_at=99999999999999999999", "offset=99999999999999999999", f"ended_at={MAX_INT + 1}", "settled_at=-1"):
        assert c.get("/v1/heals?" + q).status_code == 422, q
    assert c.get(f"/v1/heals?settled_at={MAX_INT}").status_code == 200
    ok = ",".join(["healed"] * 20)
    assert c.get("/v1/heals?outcome=" + ok).status_code == 200
    assert c.get("/v1/heals?outcome=" + ok + ",healed").status_code == 422
    assert c.get("/v1/heals?format=csv&pr_state=" + ",".join(["a"] * 21)).status_code == 422


@pytest.mark.parametrize("text", ["\n=1+1", "\x0b=1", " =1+1", " +1", "﻿=1", "​@a", " \t =HYPERLINK(1)", "\r\n-1"])
def test_whitespace_before_a_formula_does_not_hide_it(text):
    assert csv_cell(text).startswith("'")


def test_text_with_the_character_inside_is_left_alone():
    assert csv_cell("a\n=b") == "a\n=b" and csv_cell(" plain") == " plain"


def test_the_csv_is_written_in_pages_and_the_store_lock_is_free_between_them():
    store = Store(":memory:", 14)
    for i in range(25):
        store.create_heal(f"h{i:02d}", outcome="healed", repo="app")
    calls, held = [], []

    def page(at, n):
        held.append(store._lock.locked())
        calls.append((at, n))
        return store.list_heals(limit=n, offset=at)
    chunks = list(csv_chunks(page, 1000, 10))
    assert calls == [(0, 10), (10, 10), (20, 10)] and not any(held) and len(chunks) == 4
    rows = "".join(chunks).strip().split("\r\n")
    assert rows[0].split(",") == list(COLUMNS) and len(rows) == 26
    assert list(csv_chunks(page, 7, 10))[1:] and sum(len(x.splitlines()) for x in csv_chunks(page, 7, 10)) == 8


def test_the_route_streams_the_csv_with_the_same_rows():
    core = session(FakeModel(PICK))
    for i in range(30):
        core.store.create_heal(f"h{i:02d}", outcome="healed", repo="app")
    r = client(core).get("/v1/heals?format=csv&limit=25")
    assert r.status_code == 200 and len(r.text.strip().split("\r\n")) == 26 and "attachment" in r.headers["content-disposition"]


# ---- INFO: MCP total size, and advice says it is unchecked -------------------------------------------------------------------
def test_a_decoded_body_is_held_to_the_same_total_size():
    big = {**audit_body(), "padding": "x" * (10 * 1024 * 1024 + 1)}          # only the total-size check can name this one
    with pytest.raises(LimitExceeded, match="10 MB"):
        parse_request(big)
    with pytest.raises(LimitExceeded, match="10 MB"):
        parse_audit_request(big)
    with pytest.raises(ToolRefused, match="10 MB"):
        audit_locators(session(FakeModel()), big)


def test_advice_says_it_was_not_checked_against_the_live_page():
    body = request()
    body["options"] = {"mode": "advise"}
    r = client().post("/v1/heal", content=json.dumps(body)).json()
    assert r["advice"]["verified"] is False and "Not checked against the live page" in r["advice"]["note"]


# ---- follow-up counters count a state once, going forward -------------------------------------------------------------------------
def test_flip_flopping_does_not_inflate_the_followup_counters():
    core, hid = healed_core()
    c = client(core)
    for state in ("open", "merged_unchanged", "open", "merged_unchanged", "reverted", "merged_unchanged", "reverted", "open", "edited", "edited"):
        assert follow(c, hid, pr_state=state).status_code == 200
    text = core.telemetry.render()
    for state in ("open", "merged_unchanged", "reverted", "edited"):
        assert f'voyageur_followups_total{{pr_state="{state}"}} 1' in text, state


def test_a_state_that_goes_backwards_is_not_counted_and_the_record_still_follows_the_client():
    core, hid = healed_core()
    c = client(core)
    follow(c, hid, pr_state="merged_unchanged")
    follow(c, hid, pr_state="open")
    text = core.telemetry.render()
    assert 'pr_state="open"' not in text and 'voyageur_followups_total{pr_state="merged_unchanged"} 1' in text
    assert core.get(hid)["pr_state"] == "open"


# ---- the Host header ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("host", ["[::1]evil.example", "[::1]x:80", "[::1]:abc", "[::1]:", "[::1", "[::1]/", "127.0.0.1:abc", "localhost:80:90"])
def test_what_follows_a_bracketed_host_must_be_empty_or_a_port(host):
    assert client().get("/healthz", headers={"Host": host}).status_code == 403, host


@pytest.mark.parametrize("host", ["[::1]", "[::1]:8765", "localhost", "localhost:1", "127.0.0.1:65535"])
def test_the_real_loopback_forms_still_pass(host):
    assert client().get("/healthz", headers={"Host": host}).status_code == 200, host


def test_parallel_identical_followups_are_counted_once():
    # The read of pr_states_seen and its write are one step: eight clients sending the same report at once count it once.
    import threading
    import time
    from voyageur.schemas import FollowupRequest
    core, hid = healed_core()
    real_get = core.store.get_heal

    def slow_get(heal_id):
        rec = real_get(heal_id)
        time.sleep(0.02)                                   # widen the window between the read and the write
        return rec
    core.store.get_heal = slow_get
    go = threading.Barrier(8)

    def send():
        go.wait()
        core.followup(hid, FollowupRequest.model_validate({"repo": "app", "pr_state": "merged_unchanged"}))
    threads = [threading.Thread(target=send) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert 'voyageur_followups_total{pr_state="merged_unchanged"} 1' in core.telemetry.render()
