"""GET /metrics: Prometheus text exposition counted as events happen, guarded like the rest of the API."""
import re

import pytest
from fastapi.testclient import TestClient

from voyageur.app import create_app
from voyageur.schemas import HealRequest, ResultRequest
from voyageur.store import Store
from voyageur.telemetry import DURATION_BUCKETS, Telemetry
from tests.unit.scenarios import JSON, FakeModel, request, session

pytestmark = pytest.mark.unit
PICK = '{"ref": "e0", "why": "the username box"}'
OK = ResultRequest.model_validate({"count": 1, "hit": {"ref": "e0"}, "test_outcome": "passed"})


def sample(text, name, **labels):
    """The value of one series in an exposition, or None."""
    for line in text.splitlines():
        m = re.fullmatch(rf"{name}(?:\{{(.*)\}})? (\S+)", line)
        if m and dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', m.group(1) or "")) == labels:
            return float(m.group(2))
    return None


def test_counters_for_outcomes_calls_tokens_and_latency_after_a_heal():
    core = session(FakeModel(PICK))
    r = core.start(HealRequest.model_validate(request()))
    core.result(r.heal_id, OK)
    text = core.telemetry.render("1.2.3", 0)
    assert sample(text, "voyageur_heals_total", outcome="healed", mode="heal") == 1
    assert sample(text, "voyageur_model_calls_total", role="text", model="test-model", local="true", ok="true") == 1
    assert sample(text, "voyageur_model_tokens_total", role="text", model="test-model", direction="input") == 100
    assert sample(text, "voyageur_model_tokens_total", role="text", model="test-model", direction="output") == 8
    assert sample(text, "voyageur_heal_duration_seconds_count", mode="heal") == 1
    assert sample(text, "voyageur_model_call_duration_seconds_count", role="text", model="test-model") == 1
    assert sample(text, "voyageur_info", version="1.2.3") == 1 and sample(text, "voyageur_open_conversations") == 0


def test_needs_human_reasons_wrong_elements_and_advise_are_counted():
    core = session(FakeModel('{"ref": null, "none": true}'))
    core.start(HealRequest.model_validate(request()))
    body = request()
    body["options"] = {"mode": "advise"}
    core.start(HealRequest.model_validate(body))
    text = core.telemetry.render()
    assert sample(text, "voyageur_heals_total", outcome="needs_human", mode="heal") == 1
    assert sample(text, "voyageur_heals_total", outcome="needs_human", mode="advise") == 1
    assert sample(text, "voyageur_needs_human_total", reason="no_pick") == 2


def test_histogram_buckets_are_cumulative_and_end_at_inf():
    t = Telemetry()
    for v in (0.01, 0.3, 7.0, 500.0):
        t.observe("heal_duration_seconds", v, mode="heal")
    text = t.render()
    buckets = [sample(text, "voyageur_heal_duration_seconds_bucket", mode="heal", le=le)
               for le in [repr(b) if not float(b).is_integer() else str(int(b)) for b in DURATION_BUCKETS] + ["+Inf"]]
    assert buckets == sorted(buckets) and buckets[-1] == 4 and buckets[0] == 1
    assert sample(text, "voyageur_heal_duration_seconds_sum", mode="heal") == pytest.approx(507.31)


def test_every_series_has_help_and_type_and_label_values_are_escaped():
    t = Telemetry()
    t.inc("model_calls_total", role="text", model='odd"model\\name\nx', local="false", ok="true")
    text = t.render()
    assert '\\"' in text and "\\\\" in text and "\\n" in text and "odd\"model" not in text
    for name in ("heals_total", "model_calls_total", "heal_duration_seconds"):
        assert f"# HELP voyageur_{name} " in text and f"# TYPE voyageur_{name} " in text
    assert text.endswith("\n")


def test_nothing_from_a_request_is_ever_a_label():
    core = session(FakeModel(PICK))
    body = request()
    body["test"]["id"] = 'tests/test_secret_"name".py::t'
    body["run"] = {"id": "run-with-id", "repo": "private/repo"}
    core.start(HealRequest.model_validate(body))
    text = core.telemetry.render()
    assert "secret" not in text and "private/repo" not in text and "run-with-id" not in text and "username" not in text


def test_the_route_is_guarded_and_serves_the_text_exposition_format():
    c = TestClient(create_app(session(FakeModel()), "tok"), base_url="http://127.0.0.1", headers=JSON)
    assert c.get("/metrics").status_code == 401
    r = c.get("/metrics", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "# TYPE voyageur_heals_total counter" in r.text


def test_counters_do_not_need_the_store_so_retention_zero_still_counts():
    core = session(FakeModel(PICK), retention=0)
    r = core.start(HealRequest.model_validate(request()))
    core.result(r.heal_id, OK)
    assert core.store.count_rows("heals") == 0
    assert sample(core.telemetry.render(), "voyageur_model_calls_total", role="text", model="test-model", local="true", ok="true") == 1


def test_a_store_written_by_an_earlier_version_gains_the_new_columns(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE heals (heal_id TEXT PRIMARY KEY, created_at REAL NOT NULL, outcome TEXT, run_id TEXT);"
                     "INSERT INTO heals (heal_id, created_at, outcome, run_id) VALUES ('h_old', 1.0, 'healed', 'r');")
    db.commit()
    db.close()
    s = Store(path, 36500)
    assert s.get_heal("h_old")["outcome"] == "healed"
    s.update_heal("h_old", pr_state="merged_unchanged", failed_again=0, runs_since=3, mode="heal")
    assert s.get_heal("h_old")["pr_state"] == "merged_unchanged"
    assert s.count_heals(mode="heal") == 1


# ---- retention against durability ------------------------------------------------------------------------------------------
def test_capabilities_say_whether_durability_can_be_measured_with_the_retention_set():
    for days, available in ((14, False), (0, False), (30, False), (31, True), (90, True)):
        caps = session(FakeModel(), retention=days).capabilities()
        assert caps["durability"] == {"available": available, "needs_retention_days": 31}, days


def test_a_short_retention_logs_one_warning_at_startup_and_a_long_one_none(caplog, tmp_path):
    import logging
    from voyageur.core import HealSession
    caplog.set_level(logging.WARNING, logger="voyageur")
    HealSession.from_env({"HEAL_DB": str(tmp_path / "a.db"), "HEAL_WORKERS": "0"})
    warned = [r for r in caplog.records if "HEAL_RETENTION_DAYS" in r.getMessage()]
    assert len(warned) == 1 and "31" in warned[0].getMessage() and "14" in warned[0].getMessage()
    caplog.clear()
    HealSession.from_env({"HEAL_DB": str(tmp_path / "b.db"), "HEAL_WORKERS": "0", "HEAL_RETENTION_DAYS": "45"})
    assert not [r for r in caplog.records if "HEAL_RETENTION_DAYS" in r.getMessage()]
