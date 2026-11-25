"""POST /v1/audit: every locator call in the sent files ranked against the inventory, with no model call."""
import json

import pytest
from fastapi.testclient import TestClient

from voyageur.app import create_app
from voyageur.audit import MAX_LOCATORS, run_audit
from voyageur.schemas import LimitExceeded, RefusedRequest, parse_audit_request
from tests.unit.scenarios import JSON, BUTTON, PASSWORD, USER, FakeModel, session

pytestmark = pytest.mark.unit

PY_FILE = """class P:
    def go(self):
        self.page.locator('#user').fill('x')
        self.page.locator('#pwd').fill('y')
        self.page.get_by_text('Sign in').click()
        self.page.locator('#ghost').click()
        self.page.locator('input').first.click()
        self.page.locator('input').click()
        self.page.locator('div').locator('span').click()
"""
TS_FILE = """export class P {
  async go() {
    await this.page.locator('#user').fill('x');
    await this.page.getByRole('button', { name: 'Sign in' }).click();
  }
}
"""


def body(**over):
    out = {"client": {"framework": "x", "language": "python"},
           "source": {"roots": ["pages"], "files": [{"path": "pages/p.py", "content": PY_FILE},
                                                    {"path": "pages/p.ts", "content": TS_FILE}]},
           "page": {"url": "http://app.example/login", "inventory": [USER, PASSWORD, BUTTON]}}
    out.update(over)
    return out


def audit(**over):
    return run_audit(parse_audit_request(body(**over)))


def by_call(result):
    return {(r["file"], r["line"]): r for r in result["locators"]}


def test_every_locator_call_gets_a_tier_and_a_status():
    rows = by_call(audit())
    user = rows[("pages/p.py", 3)]
    assert user["kind"] == "id" and user["tier"] == 1 and user["matches"] == 1 and user["unique"] is True and user["target_ref"] == "e0"
    assert user["status"] == "upgrade" and user["better"]["kind"] == "test_id" and user["better"]["tier"] == 0
    assert user["better"]["code"] == 'self.page.get_by_test_id("login-user")' and user["better"]["locator"]["kind"] == "test_id"
    assert user["span"]["end"] > user["span"]["start"] and user["copy_based"] is False
    pwd = rows[("pages/p.py", 4)]
    assert pwd["status"] == "ok" and pwd["better"] is None and pwd["unique"] is True
    text = rows[("pages/p.py", 5)]
    assert text["kind"] == "text" and text["tier"] == 7 and text["copy_based"] is True
    assert text["status"] == "upgrade" and text["better"]["kind"] == "id" and "#go" in text["better"]["code"]
    assert rows[("pages/p.py", 6)]["status"] == "missing" and rows[("pages/p.py", 6)]["matches"] == 0
    first = rows[("pages/p.py", 7)]
    assert first["narrowed"] == ".first" and first["unique"] is True and first["matches"] == 1   # .first: one by construction
    amb = rows[("pages/p.py", 8)]
    assert amb["status"] == "ambiguous" and amb["matches"] == 2 and amb["unique"] is False and amb["better"] is None
    chain = rows[("pages/p.py", 9)]
    assert chain["kind"] == "chain" and chain["status"] == "unknown" and chain["matches"] is None


def test_a_call_that_picks_by_position_among_several_matches_is_flagged_as_a_silent_drift_risk():
    # .first on two inputs works today, but an input added above the target would be picked with no error at all: a silent
    # wrong element no heal can see (there is no failure to point at). The audit names it and offers the stable locator.
    row = by_call(audit())[("pages/p.py", 7)]
    assert row["status"] == "positional" and row["positional_of"] == 2 and row["target_ref"] == "e0"
    assert row["better"]["kind"] == "test_id" and row["better"]["code"] == 'self.page.get_by_test_id("login-user")'
    assert "position" in row["why"] and "silent" in row["why"]
    assert audit()["summary"]["by_status"]["positional"] == 1


def test_a_narrowing_on_a_call_that_is_already_unique_is_not_flagged():
    src = "class P:\n    def go(self):\n        self.page.locator('#user').first.click()\n"
    row = by_call(audit(source={"roots": ["pages"], "files": [{"path": "pages/p.py", "content": src}]}))[("pages/p.py", 3)]
    assert row["status"] != "positional" and "positional_of" not in row


def test_a_typescript_file_gets_typescript_code():
    rows = by_call(audit())
    ts_user = rows[("pages/p.ts", 3)]
    assert ts_user["status"] == "upgrade" and ts_user["better"]["code"] == "this.page.getByTestId('login-user')"
    role = rows[("pages/p.ts", 4)]
    assert role["kind"] == "role" and role["tier"] == 4 and role["status"] == "upgrade" and role["better"]["kind"] == "id"


def test_the_summary_counts_by_status_and_kind():
    out = audit()
    s = out["summary"]
    assert s["files"] == 2 and s["locators"] == len(out["locators"]) == 9 and s["upgradeable"] == 5
    assert s["by_status"]["missing"] == 1 and s["by_status"]["ambiguous"] == 1 and s["by_status"]["unknown"] == 1
    assert s["by_kind"]["id"] == 4 and s["inventory"] == 3 and out["truncated"] is False and out["skipped_files"] == []


def test_an_incomplete_inventory_never_turns_unknown_into_missing():
    out = audit(page={"url": "http://app.example/", "inventory": [USER], "inventory_more": 40})
    ghost = by_call(out)[("pages/p.py", 6)]
    assert ghost["status"] in ("unknown", "missing") and out["summary"]["inventory_complete"] is False


def test_a_better_locator_is_what_a_heal_would_write():
    """The audit suggests only what write() produces: with no test id on the element, an id call has nothing above it."""
    stripped = {k: v for k, v in USER.items() if k != "attrs"}
    stripped["attrs"] = {"id": "user"}
    rows = by_call(audit(page={"url": "http://app.example/", "inventory": [stripped, PASSWORD, BUTTON]}))
    assert rows[("pages/p.py", 3)]["status"] == "ok"


def test_the_locator_count_is_bounded_and_says_so():
    many = "\n".join(f"        self.page.locator('#id{i}').click()" for i in range(MAX_LOCATORS + 5))
    out = audit(source={"roots": [], "files": [{"path": "p.py", "content": "def f(self):\n" + many}]})
    assert len(out["locators"]) == MAX_LOCATORS and out["truncated"] is True


def test_no_model_is_called_and_no_record_is_kept():
    model = FakeModel()
    core = session(model)
    c = TestClient(create_app(core, None), base_url="http://127.0.0.1", headers=JSON)
    r = c.post("/v1/audit", content=json.dumps(body()))
    assert r.status_code == 200 and r.json()["summary"]["locators"] == 9
    assert model.calls == [] and core.store.count_rows("heals") == 0
    assert "voyageur_audits_total 1" in c.get("/metrics").text


def test_the_same_limits_and_refusals_as_a_heal_request():
    c = TestClient(create_app(session(FakeModel()), None), base_url="http://127.0.0.1", headers=JSON)
    many = body()
    many["source"]["files"] = [{"path": f"pages/f{i}.py", "content": "x"} for i in range(21)]
    assert c.post("/v1/audit", content=json.dumps(many)).status_code == 413
    assert c.post("/v1/audit", content=json.dumps(body(page={"url": "http://app.example/", "inventory": [{"tag": "a"}] * 501}))).status_code == 413
    typed = body()
    typed["page"]["inventory"] = [{"tag": "input", "value": "hunter2"}]
    r = c.post("/v1/audit", content=json.dumps(typed))
    assert r.status_code == 422 and "hunter2" not in r.text
    assert c.post("/v1/audit", content=json.dumps({**body(), "cookies": []})).status_code == 422
    assert c.post("/v1/audit", content=b"not json").status_code == 422
    assert c.post("/v1/audit", content=json.dumps({**body(), "surprise": 1})).status_code == 422


def test_the_aria_snapshot_is_redacted_at_intake_like_a_heal_request():
    req = parse_audit_request(body(page={"url": "http://app.example/", "inventory": [USER],
                                         "aria_snapshot": '- textbox "Email": jane@example.com'}))
    assert "jane@example.com" not in req.page.aria_snapshot


def test_parse_audit_request_raises_the_same_errors_as_parse_request():
    with pytest.raises(RefusedRequest):
        parse_audit_request(b"[1]")
    with pytest.raises(LimitExceeded):
        parse_audit_request(json.dumps(body(page={"url": "u", "inventory": [{"tag": "a"}] * 501})))


def test_a_hostile_file_is_skipped_not_fatal():
    out = audit(source={"roots": [], "files": [{"path": "a.py", "content": "self.page.locator('a" * 4000},
                                                {"path": "b.py", "content": "def f(self):\n        self.page.locator('#pwd').click()\n"}]})
    assert any(r["file"] == "b.py" for r in out["locators"])
