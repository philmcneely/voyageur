"""Retention bounds: the size cap never deletes young records for nothing, an aggregate reads one instant, retention 0 keeps nothing,
the rollup stays bounded, a window counts whole days only, a purge survives a bad row, and a credential-shaped name is not stored."""
import logging
import sqlite3
import threading

import pytest

from voyageur.config import ConfigError, ServiceSettings
from voyageur.metrics import aggregate
from voyageur.redact import looks_like_credential
from voyageur.rollup import OTHER
from voyageur.store import DAY, Store
from tests.unit.scenarios import FakeModel, session
from tests.unit.test_diagnose import diagnose, on
from tests.unit.test_retention import CALL, PNG, heal, new
from tests.unit.test_core import req

pytestmark = pytest.mark.unit

MB = 1024 * 1024
BASE = 30_000 * DAY                                           # midnight UTC, so a day of the rollup lines up with the window


def decision(i):
    return {"kind": "needs_human", "reason": "no_stable_locator", "ended_at": 5, "why": "x" * 580}


def fill_rollup(s, n, day):
    """n rollup rows of the day, no dimensions (the fold leaves them alone), about 100 bytes each."""
    s._db.executemany("INSERT INTO rollup (day, scope, repo, framework, language, page_language, metric, k1, k2, k3, n) "
                      "VALUES (?, '', '', '', '', '', 'filler', ?, ?, '', 1)", [(day, str(i), "y" * 60) for i in range(n)])
    s._db.commit()


# ---- 1. the cap prunes what it can free, and deletes no young heal for nothing -------------------------------------------
def test_the_cap_prunes_the_oldest_decisions_before_it_forces_a_young_heal_out():
    s, clock = new(cap=1)
    for i in range(100):
        heal(s, f"h{i}")
    clock["t"] += 7200                                        # the heals are two hours old: past the last-hour protection
    for i in range(4000):
        clock["t"] += 1
        s.cache_put(f"key{i}", decision(i))
    assert s.used_bytes() > MB
    s.purge()
    assert s.used_bytes() <= MB
    assert s.count_rows("heals") == 100 and s.rollup_info()["heals"] == 0                # no heal went for the decisions' size
    assert 0 < s.count_rows("decisions") < 4000
    assert s.cache_get("key3999") is not None and s.cache_get("key0") is None            # the oldest decisions went first


def test_a_cap_that_deleting_heals_cannot_meet_deletes_none_and_says_why_once(caplog):
    caplog.set_level(logging.WARNING, logger="voyageur")
    s, clock = new(cap=1)
    for i in range(50):
        heal(s, f"h{i}")
    clock["t"] += 7200
    fill_rollup(s, 20000, int(clock["t"] // DAY))             # a rollup that alone is over the cap: no heal can free it
    assert s.used_bytes() > MB
    s.purge()
    assert s.count_rows("heals") == 50 and s.rollup_info()["heals"] == 0                 # young heals are not deleted for nothing
    said = [r.getMessage() for r in caplog.records if "HEAL_MAX_DB_MB" in r.getMessage()]
    assert len(said) == 1 and "cannot be met" in said[0] and "rollup" in said[0] and "decisions" in said[0]
    s.purge()
    assert len([r for r in caplog.records if "HEAL_MAX_DB_MB" in r.getMessage()]) == 1  # once, not on every purge


def test_a_cap_nothing_can_meet_leaves_the_cached_decisions_alone_too():
    s, clock = new(cap=1)
    for i in range(50):
        heal(s, f"h{i}")
    clock["t"] += 7200
    for i in range(200):
        s.cache_put(f"key{i}", decision(i))
    fill_rollup(s, 20000, int(clock["t"] // DAY))             # the rollup is over the cap on its own
    s.purge()
    assert s.count_rows("decisions") == 200 and s.count_rows("heals") == 50           # neither was deleted for nothing


def test_the_cap_stops_when_the_estimate_proves_optimistic(monkeypatch):
    s, clock = new(cap=1)
    for i in range(300):
        clock["t"] += 1
        heal(s, f"h{i:03d}")
    clock["t"] += 7200
    fill_rollup(s, 6000, int(clock["t"] // DAY))              # about 0.6 MB of rollup: over the cap on its own
    monkeypatch.setattr(s, "_freeable", lambda before: 10 ** 9)                       # an estimate that promises everything
    s.max_db_bytes = s.used_bytes() - 150_000
    s.purge()
    assert s.used_bytes() > s.max_db_bytes                    # the cap was not met ...
    assert 150 < s.count_rows("heals") < 300                  # ... and it stopped after a batch that freed too little, not at zero


def test_a_cap_that_heals_can_meet_still_deletes_them_oldest_first(caplog):
    caplog.set_level(logging.WARNING, logger="voyageur")
    s, clock = new(cap=1)
    for i in range(600):
        clock["t"] += 1
        heal(s, f"h{i:03d}")
    clock["t"] += 7200
    s.max_db_bytes = s.used_bytes() - 30_000
    s.purge()
    assert s.used_bytes() <= s.max_db_bytes and 0 < s.count_rows("heals") < 600 and s.get_heal("h000") is None
    assert s.get_heal("h599") is not None and "cannot be met" not in caplog.text


# ---- 2. one read, one instant ----------------------------------------------------------------------------------------------
def due_store():
    """Ten heals one hour past retention that no purge has seen yet, and a purge that is not due for this request."""
    s, clock = new()
    for i in range(10):
        s.create_heal(f"h{i}", repo="r", framework="pw", language="python", outcome="healed", drift=1)
    clock["t"] += 31 * DAY + 3600
    s._last_purge = clock["t"]
    return s


def test_a_purge_fired_between_the_reads_of_an_aggregate_does_not_double_count(monkeypatch):
    s = due_store()
    real = s.tries_in

    def tries_in(*a, **k):                                    # another request's purge_if_due() lands here, between the reads
        s._last_purge = float("-inf")
        s.purge_if_due()
        return real(*a, **k)
    monkeypatch.setattr(s, "tries_in", tries_in)
    m = aggregate(s)
    assert (m["failures_seen"], m["healed"], m["archive"]["heals"]) == (10, 10, 0)


def test_a_purge_on_another_thread_waits_until_the_aggregate_has_read_everything(monkeypatch):
    s = due_store()
    real, ran, inside = s.tries_in, [], []

    def purge_now():
        s._last_purge = float("-inf")
        s.purge_if_due()

    def tries_in(*a, **k):
        t = threading.Thread(target=purge_now)
        t.start()
        t.join(0.3)                                           # with no exclusion the purge is over well before this
        inside.append(not t.is_alive())
        ran.append(t)
        return real(*a, **k)
    monkeypatch.setattr(s, "tries_in", tries_in)
    m = aggregate(s)
    ran[0].join(10)
    assert inside == [False]                                  # the purge was held back while the reads were made
    assert (m["failures_seen"], m["healed"], m["archive"]["heals"]) == (10, 10, 0)
    assert s.count_rows("heals") == 0 and s.rollup_info()["heals"] == 10               # and it ran afterwards
    assert aggregate(s)["failures_seen"] == 10


def test_a_snapshot_is_released_when_a_read_inside_it_fails():
    s, _ = new()
    with pytest.raises(RuntimeError):
        with s.snapshot():
            raise RuntimeError("read failed")
    s.create_heal("h1", outcome="healed")                     # the connection is usable and not stuck in a transaction
    assert s.count_rows("heals") == 1


# ---- 3. retention 0 keeps nothing, and that includes what an earlier setting left ---------------------------------------
def test_retention_zero_deletes_the_rollup_the_decisions_and_the_images_an_earlier_setting_left(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="voyageur")
    path = str(tmp_path / "s.db")
    s, clock = new(path=path)
    for i in range(5):
        heal(s, f"h{i}")
        s.add_call(f"h{i}", **CALL)
    clock["t"] += 40 * DAY
    s.purge()
    s.cache_put("k", {"kind": "healed", "ref": "e1"})
    s.put_overlay("h-late", PNG)
    s._run("INSERT INTO overlays (heal_id, png, created_at) VALUES ('orphan', ?, ?)", (PNG, clock["t"]))
    assert s.count_rows("rollup") > 0 and s.count_rows("decisions") == 1 and s.count_rows("overlays") == 2
    s.close()
    zero = Store(path, 0, lambda: clock["t"], overlay_retention_days=7)
    assert [zero.count_rows(t) for t in ("rollup", "decisions", "overlays", "heals", "calls", "tries")] == [0] * 6
    m = aggregate(zero)
    assert m["failures_seen"] == 0 and m["archive"] == {**m["archive"], "included": False, "heals": 0, "from": None, "through": None}
    assert [r for r in caplog.records if "HEAL_RETENTION_DAYS=0" in r.getMessage()]    # said once, counts only


def test_retention_zero_leaves_no_deleted_bytes_in_the_file(tmp_path):
    import os
    path = str(tmp_path / "s.db")
    s, clock = new(path=path)
    for i in range(20):
        heal(s, f"h{i}")
        s.put_overlay(f"h{i}", os.urandom(44_000))
    s.close()
    big = os.path.getsize(path)
    Store(path, 0, lambda: clock["t"]).close()
    assert big > 800_000 and os.path.getsize(path) < 100_000                                  # the pages were given back, not just freed


def test_retention_zero_adds_nothing_to_the_rollup_afterwards():
    s, clock = new(retention=0)
    heal(s, "h1")
    s.finish("h1")
    s.cache_put("k", {"kind": "healed"})
    clock["t"] += 90 * DAY
    s.purge()
    assert [s.count_rows(t) for t in ("rollup", "decisions", "heals")] == [0, 0, 0] and s.rollup_info()["heals"] == 0


# ---- 4. the rollup is bounded: old days lose their repo, framework and language, not a count -------------------------------
def spread(stores, clock, days=100, repos=12, per_day=1):
    """`per_day` heals a day for `days` days in every store, rotating repos, frameworks, client and page languages and models."""
    for i in range(days):
        for k in range(per_day):
            clock["t"] = BASE + i * DAY + 12 * 3600 + k
            n = i * per_day + k
            for s in stores:
                heal(s, f"h{n:04d}")
                s.update_heal(f"h{n:04d}", repo=f"repo-{n % repos}", framework=f"fw{n % 3}", language="python" if n % 2 else "typescript",
                              page_language="en" if n % 5 else "fr", reason=None, settled_at=3, tier_movement="up")
                s.add_call(f"h{n:04d}", **{**CALL, "model": f"m{n % 2}"})


def settle(*stores):
    """Purge until a fold has nothing left to do (a purge folds a few days at a time)."""
    for s in stores:
        for _ in range(12):
            s._last_purge = float("-inf")
            s.purge()


def detail_rows(s, before_day):
    return s._run("SELECT COUNT(*) AS n FROM rollup WHERE day < ? AND (repo <> '' OR framework <> '' OR language <> '')",
                  (before_day,))[0]["n"]


def pair(detail=40):
    clock = {"t": BASE}
    whole = Store(":memory:", 31, lambda: clock["t"], rollup_detail_days=100000)
    folded = Store(":memory:", 31, lambda: clock["t"], rollup_detail_days=detail)
    spread((whole, folded), clock, per_day=3)
    clock["t"] = BASE + 101 * DAY
    settle(whole, folded)
    return whole, folded, clock


def test_folding_old_days_reads_the_same_totals_before_and_after():
    whole, folded, clock = pair()
    cutoff = int(clock["t"] // DAY) - 40
    assert detail_rows(whole, cutoff) > 0 and detail_rows(folded, cutoff) == 0         # the old days really were folded
    assert whole.count_rows("rollup") > folded.count_rows("rollup") > 0
    assert folded.rollup_counts() == whole.rollup_counts()
    for pl in ("en", "fr"):
        assert folded.rollup_counts(page_language=pl) == whole.rollup_counts(page_language=pl)
    for model in ("m0", "m1"):
        assert folded.rollup_counts(model=model) == whole.rollup_counts(model=model)
    assert {k: v for k, v in folded.rollup_info().items() if k != "folded_before"} == {
        k: v for k, v in whole.rollup_info().items() if k != "folded_before"}
    want, got = aggregate(whole), aggregate(folded)
    for key in ("archive", "framework", "drill", "latency", "runs", "hot_spots", "locators_fixed", "truncated"):
        want.pop(key), got.pop(key)
    assert want == got and got["failures_seen"] > 50


def test_a_fold_leaves_one_row_per_day_scope_page_language_and_counter_so_the_size_no_longer_depends_on_names():
    whole, folded, _ = pair(detail=1)
    distinct = whole._run("SELECT COUNT(*) AS n FROM (SELECT DISTINCT day, scope, page_language, metric, k1, k2, k3 FROM rollup)")
    assert folded.count_rows("rollup") == distinct[0]["n"]


def test_the_days_inside_the_detail_period_keep_their_names_and_a_second_fold_changes_nothing():
    _, folded, clock = pair(detail=60)
    recent = folded._run("SELECT COUNT(*) AS n FROM rollup WHERE repo <> ''")[0]["n"]
    assert recent > 0                                                                  # the last 60 days still say which repo
    every = "SELECT * FROM rollup ORDER BY day, scope, repo, framework, language, page_language, metric, k1, k2, k3"
    rows = [tuple(r) for r in folded._run(every)]
    settle(folded)
    assert [tuple(r) for r in folded._run(every)] == rows
    assert aggregate(folded, repo="repo-1")["archive"]["heals"] > 0


def test_a_repo_filter_covers_the_detail_period_and_the_answer_says_where_the_detail_starts():
    whole, folded, clock = pair(detail=60)
    first_detail = (int(clock["t"] // DAY) - 60) * DAY
    arch = aggregate(folded, repo="repo-1")["archive"]
    assert arch["detail_days"] == 60 and arch["folded_before"] == first_detail
    assert 0 < arch["heals"] < aggregate(whole, repo="repo-1")["archive"]["heals"]     # the folded days cannot be told apart by repo
    assert aggregate(folded)["archive"]["heals"] == aggregate(whole)["archive"]["heals"]       # but the total has every day


def test_folding_runs_in_the_purge_that_follows_a_change_of_the_setting(tmp_path):
    path = str(tmp_path / "s.db")
    clock = {"t": BASE}
    a = Store(path, 31, lambda: clock["t"], rollup_detail_days=100000)
    spread((a,), clock, days=60, per_day=3)
    clock["t"] = BASE + 100 * DAY
    a.purge()
    before, total = a.count_rows("rollup"), dict(a.rollup_counts())
    a.close()
    b = Store(path, 31, lambda: clock["t"], rollup_detail_days=7)
    settle(b)
    assert b.count_rows("rollup") < before and dict(b.rollup_counts()) == total


def test_the_fold_looks_up_unfolded_days_by_an_index():
    s, _ = new()
    plan = " ".join(r["detail"] for r in s._run(
        "EXPLAIN QUERY PLAN SELECT DISTINCT day FROM rollup WHERE day < 5 AND (repo <> '' OR framework <> '' OR language <> '')"))
    assert "rollup_detail" in plan


def test_the_cap_accounts_for_the_rollup(caplog):
    caplog.set_level(logging.WARNING, logger="voyageur")
    s, clock = new(cap=1)
    heal(s, "h1")
    clock["t"] += 7200
    fill_rollup(s, 20000, int(clock["t"] // DAY))
    s.purge()
    said = [r.getMessage() for r in caplog.records if "HEAL_MAX_DB_MB" in r.getMessage()]
    assert said and "rollup" in said[0] and "MB" in said[0] and s.count_rows("heals") == 1


def test_the_detail_period_is_a_setting_with_a_default_of_90_days():
    assert ServiceSettings.from_env({}).rollup_detail_days == 90 and Store(":memory:").rollup_detail_days == 90
    assert ServiceSettings.from_env({"HEAL_ROLLUP_DETAIL_DAYS": "30"}).rollup_detail_days == 30
    for bad in ("0", "-1", "ten", "1.5"):
        with pytest.raises(ConfigError) as err:
            ServiceSettings.from_env({"HEAL_ROLLUP_DETAIL_DAYS": bad})
        assert "HEAL_ROLLUP_DETAIL_DAYS" in str(err.value) and bad not in str(err.value)


def test_from_env_hands_the_detail_period_to_the_store(tmp_path):
    from voyageur.core import HealSession
    core = HealSession.from_env({"HEAL_DB": str(tmp_path / "a.db"), "HEAL_WORKERS": "0", "HEAL_ROLLUP_DETAIL_DAYS": "45"})
    assert core.store.rollup_detail_days == 45 and core.capabilities()["rollup_detail_days"] == 45


# ---- 5. a window counts whole days of the rollup, and says which days it left out ---------------------------------------
def archived(days=60):
    """One heal at noon of each of `days` days, every one of them rolled up; and the same heals in a store that never purges."""
    clock = {"t": BASE}
    whole = Store(":memory:", 100000, lambda: clock["t"])
    cut = Store(":memory:", 31, lambda: clock["t"], rollup_detail_days=100000)            # no fold: a window is the matter here
    spread((whole, cut), clock, days=days)
    clock["t"] = BASE + 200 * DAY
    cut.purge()
    return whole, cut


def test_a_window_that_starts_inside_a_day_leaves_that_day_out_and_lists_it():
    whole, cut = archived()
    since, until = BASE + 10 * DAY + 6 * 3600, BASE + 20 * DAY + 18 * 3600           # mid-morning of day 10 to evening of day 20
    got, truth = aggregate(cut, since=since, until=until), aggregate(whole, since=since, until=until)
    assert truth["failures_seen"] == 11 and got["failures_seen"] == 9                  # days 11 to 19 are whole inside the window
    assert got["failures_seen"] <= truth["failures_seen"] <= got["failures_seen"] + sum(d["heals"] for d in got["archive"]["edge_days"])
    assert got["archive"]["edge_days"] == [{"from": BASE + 10 * DAY, "through": BASE + 11 * DAY, "heals": 1},
                                           {"from": BASE + 20 * DAY, "through": BASE + 21 * DAY, "heals": 1}]
    assert sum(got["framework"].values()) == 9                                         # the breakdown follows the same rule


def test_a_window_on_day_boundaries_has_no_edge_days_and_matches_the_records():
    whole, cut = archived()
    since, until = BASE + 10 * DAY, BASE + 21 * DAY - 1
    got = aggregate(cut, since=since, until=until)
    assert got["failures_seen"] == aggregate(whole, since=since, until=until)["failures_seen"] == 11
    assert got["archive"]["edge_days"] == []


def test_a_window_inside_one_day_counts_none_of_it_and_names_the_day():
    _, cut = archived()
    got = aggregate(cut, since=BASE + 5 * DAY + 3600, until=BASE + 5 * DAY + 7200)
    assert got["failures_seen"] == 0
    assert got["archive"]["edge_days"] == [{"from": BASE + 5 * DAY, "through": BASE + 6 * DAY, "heals": 1}]


def test_the_edge_days_follow_the_filters_and_are_empty_without_a_window():
    _, cut = archived()
    assert aggregate(cut)["archive"]["edge_days"] == []
    mine = aggregate(cut, repo="repo-5", since=BASE + 4 * DAY + 3600, until=BASE + 8 * DAY - 1)       # repo-5: days 5, 17, ...
    assert mine["archive"]["edge_days"] == [] and mine["failures_seen"] == 1
    other = aggregate(cut, repo="repo-6", since=BASE + 6 * DAY + 3600, until=BASE + 9 * DAY - 1)
    assert other["archive"]["edge_days"] == [{"from": BASE + 6 * DAY, "through": BASE + 7 * DAY, "heals": 1}]


def test_the_answer_states_the_rule_it_applied():
    _, cut = archived()
    assert "whole" in aggregate(cut, since=BASE + 10 * DAY + 5)["archive"]["edge_rule"]


# ---- 6. purge on write routes, the index, and a purge that survives a bad row --------------------------------------------
def test_a_write_route_purges_at_most_once_per_interval(monkeypatch):
    s = session(FakeModel())
    calls = []
    monkeypatch.setattr(s.store, "purge", lambda: calls.append(1))
    for _ in range(3):
        s.start(req())
    assert len(calls) == 1


def test_diagnose_purges_at_most_once_per_interval_too(monkeypatch):
    s = on()
    calls = []
    monkeypatch.setattr(s.store, "purge", lambda: calls.append(1))
    diagnose(s)
    diagnose(s)
    assert len(calls) == 1


def test_a_purge_that_is_due_again_runs_on_the_next_write():
    s = session(FakeModel())
    clock = {"t": 1_000_000.0}
    s.store.clock = lambda: clock["t"]
    seen = []
    s.store.purge = lambda: seen.append(clock["t"])
    s.start(req())
    clock["t"] += 61
    s.start(req())
    assert len(seen) == 2


OLD_DECISIONS = "CREATE TABLE decisions (key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL);"


def test_the_decisions_age_check_uses_an_index_and_a_store_without_it_gains_it(tmp_path):
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript(OLD_DECISIONS)
    db.execute("INSERT INTO decisions VALUES ('k', '{}', 1.0)")
    db.commit()
    db.close()
    s = Store(path, 31, lambda: 5.0)
    assert "decisions_created" in {r["name"] for r in s._run("PRAGMA index_list(decisions)")}
    plan = " ".join(r["detail"] for r in s._run("EXPLAIN QUERY PLAN DELETE FROM decisions WHERE created_at < 1"))
    assert "decisions_created" in plan
    assert s.count_rows("decisions") == 1                                              # the migration kept the row


def bad_store():
    """Three old heals, the middle one with a model call whose token count is text, an old decision and an old image."""
    s, clock = new()
    for hid in ("good1", "bad", "good2"):
        heal(s, hid)
    s._run("UPDATE calls SET input_tokens = ? WHERE heal_id = 'bad'", ("LEAK-ME-12345",))
    s.put_overlay("good1", PNG)
    s.cache_put("old", {"kind": "healed"})
    clock["t"] += 40 * DAY
    return s


def test_one_bad_row_is_logged_without_its_content_and_does_not_stop_the_other_phases(caplog):
    caplog.set_level(logging.INFO, logger="voyageur")
    s = bad_store()
    s.purge()
    assert s.count_rows("heals") == 1 and s.get_heal("bad") is not None and s.rollup_info()["heals"] == 2   # the good ones were counted
    assert s.count_rows("decisions") == 0 and s.count_rows("overlays") == 0                  # the later phases still ran
    assert "LEAK-ME" not in caplog.text and "12345" not in caplog.text
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR and "bad" in r.getMessage()]) == 1
    s.purge()                                                                                 # the bad one is not retried and logged again
    assert len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1
    assert s.rollup_info()["heals"] == 2


def test_a_phase_that_fails_does_not_stop_the_phases_after_it_and_logs_no_error_text(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="voyageur")
    s = bad_store()
    s._run("DELETE FROM calls WHERE heal_id = 'bad'")
    monkeypatch.setattr(s, "_expire_images", lambda *a: (_ for _ in ()).throw(RuntimeError("LEAK-ME-disk")))
    s.purge()
    assert s.count_rows("heals") == 0 and s.count_rows("decisions") == 0                      # records before it, decisions after it
    assert [r for r in caplog.records if "purge failed" in r.getMessage() and "images" in r.getMessage()]
    assert "LEAK-ME" not in caplog.text and "Traceback" not in caplog.text                   # the class, never the message or a trace


def test_the_quarantine_fits_in_one_statement_on_any_sqlite():
    from voyageur.store import MAX_QUARANTINE
    assert MAX_QUARANTINE + 2 <= 999                                                          # the old limit on bound variables


# ---- 7. a name that looks like a credential is not stored -------------------------------------------------------------------
TOKENS = {
    "github classic": "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5",
    "github fine-grained": "github_pat_" + "11ABCDEFG0aB3dE5gH7jK9_mN1pQ3sT5vW7yZ9bC1dE3fG5hJ7kL9",
    "openai style": "sk-" + "proj-aB3dE5gH7jK9mN1pQ3sT5vW7yZ9",
    "slack": "xoxb-" + "1234567890-aB3dE5gH7jK9mN1pQ3sT5v",
    "aws": "AKIA" + "IOSFODNN7EXAMPLE",
    "high entropy": "Zk3Jq8Wm2Xv9Lp4Rt7Yn1Bc6Hd5Fg0Sa",
    "hex digest": "9f86d081884c7d659a2feaa0c55ad015",
    "inside a path": "acme/" + "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5",
    "payment key": "sk_live_" + "aB3dE5gH7jK9mN1pQ3sT",
    "model hub": "hf_" + "aBcDeFgHiJkLmNoPqRsTuVwXyZaBcDeFgH",
    "lower and digits": "k3j9x2m8q1w7e4r6t5y0u3i8o2p9a1s5",
}
KEEP = ["owner/repo", "playwright-ai-framework-ts", "task-management-dashboard-frontend", "my app v2.1", "qa_eval/service-2024",
        "a" * 100, "disk-usage-monitor", "sk-learn-things", "ghp-notes", "playwright", "en-US", "pytest",
        "web-sk-checkout-service-frontend", "sk-learn-extra-long-experiment-name", "acme/payments-api-gateway-service-v2"]


@pytest.mark.parametrize("name", TOKENS.values(), ids=TOKENS.keys())
def test_a_credential_shaped_name_is_filed_under_other(name):
    assert looks_like_credential(name)
    s, clock = new()
    heal(s, "h1")
    s.update_heal("h1", repo=name, framework=name)            # the two names a client chooses (its language is python or typescript)
    clock["t"] += 40 * DAY
    s.purge()
    row = s._run("SELECT DISTINCT repo, framework, language, page_language FROM rollup")
    assert [tuple(r) for r in row] == [(OTHER, OTHER, "python", "")]
    assert name not in " ".join(str(v) for r in s._run("SELECT * FROM rollup") for v in tuple(r))


@pytest.mark.parametrize("name", KEEP)
def test_an_ordinary_name_is_kept(name):
    assert not looks_like_credential(name)
    s, clock = new()
    heal(s, "h1")
    s.update_heal("h1", repo=name)
    clock["t"] += 40 * DAY
    s.purge()
    assert {r["repo"] for r in s._run("SELECT DISTINCT repo FROM rollup")} == {name}
