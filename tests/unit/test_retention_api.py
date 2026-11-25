"""Retention settings, the API for a heal whose image expired, the dashboard and the CLI help."""
import logging

import pytest
from fastapi.testclient import TestClient

from voyageur.app import create_app
from voyageur.cli import main
from voyageur.config import ConfigError, ServiceSettings
from voyageur.core import HealSession
from voyageur.dashboard import page
from voyageur.store import DAY, Store
from tests.unit.scenarios import ENDPOINT, JSON, FakeModel

pytestmark = pytest.mark.unit

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
HEAD = {"Authorization": "Bearer tok"}


# ---- settings -------------------------------------------------------------------------------------------------------
def test_the_defaults_are_31_days_of_records_7_of_images_and_no_cap():
    s = ServiceSettings.from_env({})
    assert (s.retention_days, s.overlay_retention_days, s.max_db_mb) == (31, 7, 0)


def test_the_new_settings_are_read_from_the_environment():
    s = ServiceSettings.from_env({"HEAL_RETENTION_DAYS": "60", "HEAL_OVERLAY_RETENTION_DAYS": "3", "HEAL_MAX_DB_MB": "500"})
    assert (s.retention_days, s.overlay_retention_days, s.max_db_mb) == (60, 3, 500)


def test_zero_is_a_value_for_each_setting_and_means_what_the_docs_say():
    s = ServiceSettings.from_env({"HEAL_RETENTION_DAYS": "0", "HEAL_OVERLAY_RETENTION_DAYS": "0", "HEAL_MAX_DB_MB": "0"})
    assert (s.retention_days, s.overlay_retention_days, s.max_db_mb) == (0, 0, 0)     # keep nothing, keep no image, no cap


@pytest.mark.parametrize("var", ["HEAL_RETENTION_DAYS", "HEAL_OVERLAY_RETENTION_DAYS", "HEAL_MAX_DB_MB"])
@pytest.mark.parametrize("bad", ["-1", "ten", "1.5", "sk-secret-value"])
def test_an_invalid_value_names_the_variable_and_never_the_value(var, bad):
    with pytest.raises(ConfigError) as err:
        ServiceSettings.from_env({var: bad})
    assert var in str(err.value) and bad not in str(err.value)


@pytest.mark.parametrize("var", ["HEAL_OVERLAY_RETENTION_DAYS", "HEAL_MAX_DB_MB"])
def test_a_blank_value_is_unset_so_the_default_applies(var):
    assert ServiceSettings.from_env({var: "  "}) == ServiceSettings.from_env({})


def test_from_env_hands_every_limit_to_the_store(tmp_path):
    core = HealSession.from_env({"HEAL_DB": str(tmp_path / "a.db"), "HEAL_WORKERS": "0", "HEAL_RETENTION_DAYS": "45",
                                 "HEAL_OVERLAY_RETENTION_DAYS": "2", "HEAL_MAX_DB_MB": "7"})
    st = core.store
    assert (st.retention_days, st.overlay_retention_days, st.max_db_bytes) == (45, 2, 7 * 1024 * 1024)


def test_the_durability_warning_still_fires_below_the_window_and_not_at_the_default(caplog, tmp_path):
    caplog.set_level(logging.WARNING, logger="voyageur")
    HealSession.from_env({"HEAL_DB": str(tmp_path / "a.db"), "HEAL_WORKERS": "0"})                     # the new default, 31
    assert not [r for r in caplog.records if "HEAL_RETENTION_DAYS" in r.getMessage()]
    HealSession.from_env({"HEAL_DB": str(tmp_path / "b.db"), "HEAL_WORKERS": "0", "HEAL_RETENTION_DAYS": "30"})
    assert len([r for r in caplog.records if "HEAL_RETENTION_DAYS" in r.getMessage()]) == 1


def test_capabilities_report_the_limits():
    caps = HealSession(ServiceSettings(), Store(":memory:"), FakeModel(), ENDPOINT).capabilities()
    assert caps["retention_days"] == 31 and caps["overlay_retention_days"] == 7 and caps["max_db_mb"] == 0
    assert caps["durability"] == {"available": True, "needs_retention_days": 31}


# ---- the API for an expired image ------------------------------------------------------------------------------------
def build(overlay_days=7):
    clock = {"t": 3_000_000.0}
    store = Store(":memory:", 31, lambda: clock["t"], overlay_retention_days=overlay_days)
    core = HealSession(ServiceSettings(overlay_retention_days=overlay_days), store, FakeModel(), ENDPOINT)
    store.create_heal("h1", outcome="healed", drift=1, mode="heal", repo="app")
    store.put_overlay("h1", PNG)
    return clock, store, TestClient(create_app(core, "tok"), base_url="http://127.0.0.1", headers={**JSON, **HEAD})


def test_the_image_is_served_while_kept_and_the_record_says_so():
    clock, store, c = build()
    r = c.get("/v1/heals/h1/overlay.png")
    assert r.status_code == 200 and r.content == PNG
    assert c.get("/v1/heals/h1").json()["overlay"]["state"] == "kept"


def test_an_expired_image_is_a_410_that_says_it_expired_and_the_record_still_answers():
    clock, store, c = build()
    clock["t"] += 8 * DAY
    r = c.get("/v1/heals/h1/overlay.png")
    assert r.status_code == 410 and "expired" in r.json()["detail"] and "HEAL_OVERLAY_RETENTION_DAYS" in r.json()["detail"]
    rec = c.get("/v1/heals/h1")
    assert rec.status_code == 200 and rec.json()["outcome"] == "healed"
    assert rec.json()["overlay"]["state"] == "expired" and rec.json()["overlay"]["expired_at"] is not None
    assert c.get("/v1/heals").json()["total"] == 1


def test_a_heal_with_no_image_is_still_a_404_and_an_unknown_heal_too():
    clock, store, c = build()
    store.create_heal("h2", outcome="healed", mode="heal")
    assert c.get("/v1/heals/h2/overlay.png").status_code == 404 and c.get("/v1/heals/nope/overlay.png").status_code == 404
    assert c.get("/v1/heals/h2").json()["overlay"] == {"state": "none"}


def test_a_heal_whose_record_expired_is_a_404_as_before():
    clock, store, c = build()
    clock["t"] += 40 * DAY
    assert c.get("/v1/heals/h1/overlay.png").status_code == 404 and c.get("/v1/heals/h1").status_code == 404


def test_the_overlay_image_is_never_stored_with_a_period_of_zero():
    clock, store, c = build(overlay_days=0)
    assert c.get("/v1/heals/h1/overlay.png").status_code == 404 and c.get("/v1/heals/h1").json()["overlay"]["state"] == "none"


# ---- the dashboard ----------------------------------------------------------------------------------------------------
def test_the_dashboard_reads_the_overlay_state_and_says_when_an_image_expired():
    html, _ = page()
    assert "rec.overlay" in html and "expired" in html and "Overlay image" in html
    assert "rec.archive" not in html and "m.archive" in html                          # the overview reads what the archive holds


def test_the_dashboard_tells_where_the_totals_come_from():
    html, _ = page()
    assert "m.archive.heals" in html and "retained_only" in html


# ---- the CLI help ------------------------------------------------------------------------------------------------------
def test_the_help_names_the_retention_settings(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for name in ("HEAL_RETENTION_DAYS", "HEAL_OVERLAY_RETENTION_DAYS", "HEAL_MAX_DB_MB"):
        assert name in out, name
