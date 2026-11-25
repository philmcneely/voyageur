"""Retention: heal records, overlay images, the size cap and the counts-only rollup, all on an injected clock."""
import logging
import os
import sqlite3

import pytest

from voyageur.store import DAY, DURABLE_DAYS, Store

pytestmark = pytest.mark.unit

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 400
CALL = dict(role="text", model="m", local=True, input_tokens=100, output_tokens=8, images=0, latency_ms=5, ok=True, step=3)


def new(retention=31, overlay=7, cap=0, path=":memory:", now=1_000_000.0):
    clock = {"t": now}
    return Store(path, retention, lambda: clock["t"], overlay_retention_days=overlay, max_db_mb=cap), clock


def heal(s, hid, **fields):
    s.create_heal(hid, run_id="r1", repo="app", framework="fw", language="python", drift=1, outcome="healed", covers=1,
                  mode="heal", **fields)
    s.add_call(hid, **CALL)


# ---- defaults -------------------------------------------------------------------------------------------------------
def test_the_defaults_keep_a_record_31_days_and_an_image_7():
    s = Store(":memory:")
    assert (s.retention_days, s.overlay_retention_days, s.max_db_bytes) == (31, 7, 0)


# ---- images --------------------------------------------------------------------------------------------------------
def test_an_image_expires_after_its_own_period_and_the_heal_record_stays():
    s, clock = new()
    heal(s, "h1")
    s.put_overlay("h1", PNG)
    clock["t"] += 6 * DAY
    s.purge()
    assert s.get_overlay("h1") == PNG and s.overlay_state("h1")["state"] == "kept"
    clock["t"] += 2 * DAY                                          # 8 days old: past the 7
    s.purge()
    assert s.get_overlay("h1") is None
    state = s.overlay_state("h1")
    assert state["state"] == "expired" and state["expired_at"] == clock["t"]
    rec = s.get_heal("h1")
    assert rec is not None and rec["outcome"] == "healed" and len(rec["calls"]) == 1       # the record is untouched
    assert rec["overlay"]["state"] == "expired"
    assert s._run("SELECT length(png) AS n FROM overlays")[0]["n"] == 0                    # the bytes are gone, not the row


def test_a_kept_image_is_reported_with_the_time_it_will_expire():
    s, clock = new()
    heal(s, "h1")
    s.put_overlay("h1", PNG)
    state = s.get_heal("h1")["overlay"]
    assert state["state"] == "kept" and state["expires_at"] == clock["t"] + 7 * DAY and state["bytes"] == len(PNG)


def test_a_heal_without_an_image_says_none():
    s, _ = new()
    heal(s, "h1")
    assert s.get_heal("h1")["overlay"] == {"state": "none"} and s.overlay_state("nope") == {"state": "none"}


def test_purging_images_twice_changes_nothing_the_second_time():
    s, clock = new()
    heal(s, "h1")
    s.put_overlay("h1", PNG)
    clock["t"] += 8 * DAY
    s.purge()
    first = s.overlay_state("h1")["expired_at"]
    clock["t"] += 100
    s.purge()
    assert s.overlay_state("h1")["expired_at"] == first and s.count_rows("overlays") == 1


def test_an_image_period_of_zero_stores_no_image_at_all():
    s, _ = new(overlay=0)
    heal(s, "h1")
    s.put_overlay("h1", PNG)
    assert s.count_rows("overlays") == 0 and s.get_overlay("h1") is None and s.get_heal("h1")["overlay"]["state"] == "none"


def test_an_image_never_outlives_its_record():
    s, clock = new(retention=3, overlay=10)
    heal(s, "h1")
    s.put_overlay("h1", PNG)
    clock["t"] += 4 * DAY
    s.purge()
    assert s.get_heal("h1") is None and s.count_rows("overlays") == 0


def test_lowering_the_image_period_expires_images_already_stored(tmp_path):
    path = str(tmp_path / "s.db")
    s, clock = new(path=path)
    heal(s, "h1")
    s.put_overlay("h1", PNG)
    s.close()
    again = Store(path, 31, lambda: clock["t"] + DAY, overlay_retention_days=0)
    again.purge()
    assert again.get_overlay("h1") is None and again.overlay_state("h1")["state"] == "expired"
    assert again.get_heal("h1") is not None


# ---- records -------------------------------------------------------------------------------------------------------
def test_a_record_is_kept_31_days_by_default_and_then_purged():
    s, clock = new()
    heal(s, "h1")
    clock["t"] += 30 * DAY
    assert s.get_heal("h1") is not None
    clock["t"] += 2 * DAY
    s.purge()
    assert s.get_heal("h1") is None and s.count_rows("calls") == 0


def test_retention_zero_still_keeps_nothing_and_rolls_nothing_up():
    s, _ = new(retention=0)
    heal(s, "h1")
    s.finish("h1")
    assert s.get_heal("h1") is None and s.count_rows("rollup") == 0 and s.rollup_info()["heals"] == 0


# ---- the rollup ----------------------------------------------------------------------------------------------------
def test_a_purged_heal_is_counted_in_the_rollup_before_it_goes():
    s, clock = new()
    heal(s, "h1", reason=None, settled_at=3, tier_movement="up", page_language="en")
    s.add_call("h1", **{**CALL, "model": "big", "local": False, "images": 1, "role": "vision"})
    s.add_try("h1", 1, "e1", "testid", "page.get_by_test_id('x')", "wrong_element")
    clock["t"] += 40 * DAY
    s.purge()
    assert s.get_heal("h1") is None
    counts = s.rollup_counts()
    assert counts[("heals", "", "", "")] == 1 and counts[("healed", "", "", "")] == 1
    assert counts[("calls", "", "", "")] == 2 and counts[("in", "", "", "")] == 200
    assert counts[("rejected", "", "", "")] == 1
    assert counts[("cm_calls", "vision", "big", "0")] == 1
    assert s.rollup_info()["heals"] == 1


def test_the_rollup_holds_counts_only_no_text_of_the_heal():
    s, clock = new()
    heal(s, "h1", test_id="tests/test_secret.py::test_hush", file="pages/secret_page.py", line=7, page_path="/private/area",
         broken_locator="#secret-field", final_locator="get_by_test_id('hush')", locator_key="pages/secret_page.py|#secret-field",
         why="the secret why", evidence={"note": "secret evidence"}, verify_ref="e9", pr_state="merged_unchanged")
    s.add_try("h1", 1, "e1", "testid", "page.get_by_test_id('hush')", "ok", "secret detail")
    clock["t"] += 40 * DAY
    s.purge()
    dump = " ".join(str(v) for row in s._run("SELECT * FROM rollup") for v in tuple(row))
    for text in ("secret", "hush", "private", "tests/", "pages/", "#", "get_by", "h1"):
        assert text not in dump, text
    columns = {r["name"] for r in s._run("PRAGMA table_info(rollup)")}
    assert columns == {"day", "scope", "repo", "framework", "language", "page_language", "metric", "k1", "k2", "k3", "n"}


def test_a_client_cannot_grow_the_rollup_without_bound_by_inventing_names():
    from voyageur.rollup import MAX_DIM_VALUES, OTHER
    s, clock = new()
    for i in range(MAX_DIM_VALUES + 50):
        clock["t"] += 1
        heal(s, f"h{i}")
        s.update_heal(f"h{i}", repo=f"repo-{i}", framework="x" * 300)
    clock["t"] += 40 * DAY
    s.purge()
    repos = {r["repo"] for r in s._run("SELECT DISTINCT repo FROM rollup")}
    assert len(repos) == MAX_DIM_VALUES + 1 and OTHER in repos                   # the extra names are filed under "other"
    assert max(len(r["framework"]) for r in s._run("SELECT DISTINCT framework FROM rollup")) <= 100
    assert s.rollup_info()["heals"] == MAX_DIM_VALUES + 50                       # and every heal is still counted
    assert s.rollup_counts()[("heals", "", "", "")] == MAX_DIM_VALUES + 50


def test_the_rollup_holds_no_framework_name_as_a_counter_key():
    s, clock = new()
    for i in range(30):
        clock["t"] += 1
        heal(s, f"h{i}")
        s.update_heal(f"h{i}", framework=f"fw-{i}")
    clock["t"] += 40 * DAY
    s.purge()
    assert not s._run("SELECT 1 FROM rollup WHERE metric = 'framework'")
    assert len({r["framework"] for r in s._run("SELECT DISTINCT framework FROM rollup")}) == 30


def test_the_combinations_of_names_are_bounded_too():
    from voyageur.rollup import MAX_DIM_COMBOS, OTHER
    s, clock = new()
    n = MAX_DIM_COMBOS + 40
    for i in range(n):
        clock["t"] += 1
        heal(s, f"h{i}")
        s.update_heal(f"h{i}", repo=f"r{i % 150}", framework=f"f{i % 7}", page_language="en" if i % 2 else "fr")
    clock["t"] += 40 * DAY
    s.purge()
    combos = s._run("SELECT DISTINCT repo, framework, language, page_language FROM rollup")
    assert len(combos) <= MAX_DIM_COMBOS + 1 and s.rollup_info()["heals"] == n
    assert any(r["repo"] == OTHER for r in combos)


@pytest.mark.parametrize("name", ["https://user:tok@host/x", "repo?token=1", "a\nb", "r@x"])
def test_a_name_that_could_carry_a_credential_is_filed_under_other(name):
    from voyageur.rollup import OTHER
    s, clock = new()
    heal(s, "h1")
    s.update_heal("h1", repo=name)
    clock["t"] += 40 * DAY
    s.purge()
    assert {r["repo"] for r in s._run("SELECT DISTINCT repo FROM rollup")} == {OTHER}


def test_purging_twice_counts_a_heal_once():
    s, clock = new()
    heal(s, "h1")
    clock["t"] += 40 * DAY
    s.purge()
    once = dict(s.rollup_counts())
    s.purge()
    s.purge()
    assert dict(s.rollup_counts()) == once and s.rollup_info()["heals"] == 1


def test_the_rollup_is_added_to_in_the_same_step_that_deletes_so_a_failure_loses_nothing(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="voyageur")
    s, clock = new()
    heal(s, "h1")
    clock["t"] += 40 * DAY
    real = s._delete_ids

    def boom(ids):
        real(ids)
        raise RuntimeError("disk")
    monkeypatch.setattr(s, "_delete_ids", boom)
    s.purge()                                                                  # a failing purge never breaks the request
    assert s.count_rows("heals") == 1 and s.count_rows("rollup") == 0          # rolled back as one step
    assert [r for r in caplog.records if "purge failed" in r.getMessage()]
    monkeypatch.undo()
    s.purge()
    assert s.count_rows("heals") == 0 and s.rollup_info()["heals"] == 1


def test_a_durable_heal_is_counted_as_held_when_it_is_rolled_up():
    s, clock = new()
    heal(s, "h1", pr_state="merged_unchanged", runs_since=12, days_since=31, failed_again=0, followup_at=1.0)
    heal(s, "h2", pr_state="merged_unchanged", runs_since=3, days_since=5, failed_again=0, followup_at=1.0)
    heal(s, "h3", pr_state="merged_unchanged", runs_since=12, days_since=31, failed_again=1, followup_at=1.0)
    clock["t"] += 40 * DAY
    s.purge()
    c = s.rollup_counts()
    assert (c[("due", "", "", "")], c[("durable", "", "", "")], c[("pending", "", "", "")], c[("broke", "", "", "")]) == (2, 1, 1, 1)


def test_rollup_counts_filter_by_dimension_and_day_window():
    s, clock = new()
    heal(s, "a", )
    s.update_heal("a", repo="one")
    clock["t"] += 5 * DAY
    heal(s, "b")
    s.update_heal("b", repo="two")
    clock["t"] += 60 * DAY
    s.purge()
    assert s.rollup_counts()[("heals", "", "", "")] == 2
    assert s.rollup_counts(repo="one")[("heals", "", "", "")] == 1
    assert s.rollup_counts(repo="nobody")[("heals", "", "", "")] == 0
    first = 1_000_000.0
    assert s.rollup_counts(since=first + 4 * DAY)[("heals", "", "", "")] == 1
    assert s.rollup_counts(until=first + DAY)[("heals", "", "", "")] == 1
    assert s.rollup_counts(model="m")[("heals", "", "", "")] == 2          # both used model m
    assert s.rollup_counts(model="other")[("heals", "", "", "")] == 0


# ---- migration of a store written by the version before this one -----------------------------------------------------
OLD_SCHEMA = """
CREATE TABLE heals (heal_id TEXT PRIMARY KEY, created_at REAL NOT NULL, run_id TEXT, repo TEXT, outcome TEXT, drift INTEGER,
  mode TEXT, covers INTEGER, finished_at REAL);
CREATE TABLE calls (id INTEGER PRIMARY KEY AUTOINCREMENT, heal_id TEXT NOT NULL, created_at REAL NOT NULL, role TEXT, model TEXT,
  local INTEGER, input_tokens INTEGER, output_tokens INTEGER, images INTEGER, latency_ms INTEGER, ok INTEGER, step INTEGER);
CREATE TABLE tries (id INTEGER PRIMARY KEY AUTOINCREMENT, heal_id TEXT NOT NULL, round INTEGER, ref TEXT, kind TEXT, code TEXT,
  result TEXT, detail TEXT, created_at REAL NOT NULL);
CREATE TABLE overlays (heal_id TEXT PRIMARY KEY, png BLOB NOT NULL, created_at REAL NOT NULL);
CREATE TABLE decisions (key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL);
"""


def old_store(path, now):
    db = sqlite3.connect(path)
    db.executescript(OLD_SCHEMA)
    for hid, age in (("ancient", 50), ("recent", 2), ("middle", 20)):
        db.execute("INSERT INTO heals (heal_id, created_at, run_id, repo, outcome, drift, mode, covers) VALUES (?,?,?,?,?,?,?,?)",
                   (hid, now - age * DAY, "r", "app", "healed", 1, "heal", 1))
        db.execute("INSERT INTO calls (heal_id, created_at, role, model, local, input_tokens, output_tokens, images, latency_ms, ok, step) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (hid, now - age * DAY, "text", "m", 1, 10, 2, 0, 5, 1, 3))
        db.execute("INSERT INTO overlays (heal_id, png, created_at) VALUES (?,?,?)", (hid, PNG, now - age * DAY))
    db.commit()
    db.close()


def test_a_store_of_the_earlier_version_opens_and_upgrades_without_losing_data(tmp_path):
    now = 5_000_000.0
    path = str(tmp_path / "old.db")
    old_store(path, now)
    s = Store(path, 31, lambda: now)
    assert {r["name"] for r in s._run("PRAGMA table_info(overlays)")} >= {"heal_id", "png", "created_at", "expired_at"}
    s.purge()
    assert s.get_heal("recent")["outcome"] == "healed" and s.get_heal("middle") is not None      # kept, with their calls
    assert len(s.get_heal("recent")["calls"]) == 1
    assert s.get_heal("ancient") is None                                                       # older than 31 days: rolled up
    assert s.rollup_counts()[("heals", "", "", "")] == 1 and s.rollup_counts()[("calls", "", "", "")] == 1
    assert s.get_overlay("recent") == PNG and s.overlay_state("recent")["state"] == "kept"      # 2 days old, under 7
    assert s.overlay_state("middle")["state"] == "expired" and s.get_heal("middle")["overlay"]["state"] == "expired"
    s.close()
    again = Store(path, 31, lambda: now)                                                       # opening it again is harmless
    again.purge()
    assert again.rollup_counts()[("heals", "", "", "")] == 1 and again.count_rows("heals") == 2


# ---- the size cap -------------------------------------------------------------------------------------------------------
def fat(s, clock, n, step=3600.0, start_day=0.0):
    """n heals, each with a ~44 KB image, one every `step` seconds."""
    for i in range(n):
        clock["t"] = 1_000_000.0 + start_day * DAY + i * step
        heal(s, f"h{i:03d}")
        s.put_overlay(f"h{i:03d}", os.urandom(44_000))


def test_the_cap_is_off_by_default():
    s, clock = new()
    fat(s, clock, 40)
    s.purge()
    assert s.count_rows("overlays") == 40 and all(s.overlay_state(f"h{i:03d}")["state"] == "kept" for i in range(40))


def test_the_cap_expires_the_oldest_images_first_and_keeps_every_record():
    s, clock = new(cap=1)
    fat(s, clock, 60)                                     # about 2.6 MB of images against a 1 MB cap
    s.purge()
    assert s.used_bytes() <= 1024 * 1024
    assert s.count_rows("heals") == 60                    # no record was needed
    states = [s.overlay_state(f"h{i:03d}")["state"] for i in range(60)]
    assert states[0] == "expired" and states[-1] == "kept"
    assert states == sorted(states, key=lambda x: x != "expired")                # the expired ones are the oldest, all in a row
    assert s.get_overlay("h059") is not None


def crowd(s, clock, n, tag, at, images=0):
    """n heals one second apart from `at`, the first `images` of them with a ~44 KB image."""
    for i in range(n):
        clock["t"] = at + i
        heal(s, f"{tag}{i:03d}")
        if i < images:
            s.put_overlay(f"{tag}{i:03d}", os.urandom(44_000))


def test_the_cap_frees_images_before_it_touches_a_record():
    s, clock = new(retention=90, cap=1)
    now = 5_000_000.0
    crowd(s, clock, 100, "old", now - 40 * DAY)
    crowd(s, clock, 100, "new", now - DAY, images=20)
    clock["t"] = now
    s.max_db_bytes = s.used_bytes() - 200_000            # the 880 KB of images cover it
    s.purge()
    assert s.used_bytes() <= s.max_db_bytes
    assert s.count_rows("heals") == 200 and s.rollup_info()["heals"] == 0
    assert s.overlay_state("new000")["state"] == "expired"       # the oldest image went first
    assert s.overlay_state("new019")["state"] == "kept"


def test_the_cap_goes_to_records_only_when_every_image_is_gone_and_counts_them_first():
    s, clock = new(retention=90, cap=1)
    now = 5_000_000.0
    crowd(s, clock, 100, "old", now - 40 * DAY)
    crowd(s, clock, 100, "new", now - DAY, images=5)
    clock["t"] = now
    s.max_db_bytes = s.used_bytes() - 400_000            # more than five images can give
    s.purge()
    assert s._run("SELECT COALESCE(SUM(length(png)), 0) AS n FROM overlays")[0]["n"] == 0
    gone = 200 - s.count_rows("heals")
    assert gone > 0 and s.rollup_info()["heals"] == gone              # each purged record was counted first
    assert s.used_bytes() <= s.max_db_bytes or s.count_rows("heals") == 0


def test_the_cap_purges_records_past_the_durability_window_before_younger_ones(caplog):
    caplog.set_level(logging.WARNING, logger="voyageur")
    s, clock = new(retention=90, cap=1)
    now = 5_000_000.0
    crowd(s, clock, 300, "old", now - (DURABLE_DAYS + 15) * DAY)
    crowd(s, clock, 300, "new", now - 5 * DAY)
    clock["t"] = now
    s.max_db_bytes = s.used_bytes() - 30_000              # a fraction of what the old records take
    s.purge()
    assert s.used_bytes() <= s.max_db_bytes
    assert all(s.get_heal(f"new{i:03d}") is not None for i in range(300))
    assert 0 < 600 - s.count_rows("heals") < 300 and s.get_heal("old000") is None
    assert not [r for r in caplog.records if "younger than" in r.getMessage()]


def test_the_cap_may_force_a_young_record_out_and_then_warns(caplog):
    caplog.set_level(logging.WARNING, logger="voyageur")
    s, clock = new(retention=90, cap=1)
    now = 5_000_000.0
    crowd(s, clock, 600, "y", now - 5 * DAY)
    clock["t"] = now
    s.max_db_bytes = s.used_bytes() - 30_000
    s.purge()
    assert s.used_bytes() <= s.max_db_bytes
    gone = 600 - s.count_rows("heals")
    assert gone > 0 and s.rollup_info()["heals"] == gone
    warned = [r.getMessage() for r in caplog.records if "HEAL_MAX_DB_MB" in r.getMessage()]
    assert len(warned) == 1 and f"{gone} heal records younger than {DURABLE_DAYS} days" in warned[0]
    assert s.get_heal("y599") is not None and s.get_heal("y000") is None          # the oldest went first


def test_a_cap_nothing_can_meet_ends_and_says_so(caplog):
    caplog.set_level(logging.WARNING, logger="voyageur")
    s, clock = new(cap=1)
    heal(s, "h1")
    clock["t"] += 7200
    s.max_db_bytes = 10
    s.purge()
    assert s.count_rows("heals") == 0 and [r for r in caplog.records if "cannot get under" in r.getMessage()]
    s.purge()                                                                  # nothing left: no second warning
    assert len([r for r in caplog.records if "cannot get under" in r.getMessage()]) == 1


def test_the_cap_never_removes_a_heal_from_the_last_hour_or_one_still_open():
    s, clock = new(cap=1)
    heal(s, "fresh")
    s.update_heal("fresh", outcome="try")
    clock["t"] += 600
    s.max_db_bytes = 10
    s.purge()
    assert s.get_heal("fresh") is not None and s.rollup_info()["heals"] == 0


def test_the_cap_ends_when_deleting_frees_nothing_and_leaves_the_protected_heals(monkeypatch):
    s, clock = new(cap=1)
    for i in range(30):
        heal(s, f"h{i}")
    clock["t"] += 7200
    heal(s, "fresh")
    s.max_db_bytes = 10
    monkeypatch.setattr(s, "used_bytes", lambda: 10 + 7500)                    # over by 7500, and deleting never lowers it
    s.purge()
    assert s.count_rows("heals") == 1 and s.get_heal("fresh") is not None         # it ends, with the last-hour heal still there


def test_the_size_is_the_pages_in_use_so_it_falls_when_rows_are_deleted():
    s, clock = new()
    fat(s, clock, 20)
    big = s.used_bytes()
    clock["t"] += 40 * DAY
    s.purge()
    assert s.used_bytes() < big / 2


def test_a_cap_purge_twice_changes_nothing_the_second_time():
    s, clock = new(cap=1)
    fat(s, clock, 60)
    s.purge()
    kept = (s.count_rows("heals"), dict(s.rollup_counts()), s.used_bytes())
    s.purge()
    assert (s.count_rows("heals"), dict(s.rollup_counts()), s.used_bytes()) == kept
