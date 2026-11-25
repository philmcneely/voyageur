"""
===============================================================================
Audit Store (the heal record, in SQLite)
===============================================================================
One row per heal with every field the metrics need: where it happened, what broke, how it ended (outcome, reason code,
the step that settled the target and the step it ended at, rounds, candidates tried and rejected), what was written (final
locator, tier movement, language branch, how many tests the fix covered), what it cost (one row per model call: role, model,
local or cloud, tokens, images, latency) and, once a client reports it after the PR, whether a human kept it and whether it
stayed green. Candidates tried are rows of their own, so every "wrong element" count can be opened.

The store holds decisions, usage and short facts about the page (its path and language, counts), never file contents, page
text, the client's screenshot or model replies. The one picture kept is the overlay (the marked, cropped screenshot a vision model
was shown), in a table of its own, so a reviewer can see what the model saw. It is images that fill a store (about 44 KB each
against about 1.4 KB for a heal record), so an image has a shorter life than its record: after HEAL_OVERLAY_RETENTION_DAYS the
bytes are deleted and the heal keeps a marker that its image expired. A record that is purged first has its counts added to a daily
rollup (rollup.py), so the totals on /v1/metrics outlive the records. The decision cache lives here too: a repeat of the same
drift (same broken call, same page inventory, same language) costs no tokens.

Features:
    ✓ SQLite through the standard library, one connection behind a lock (the HTTP layer runs handlers in threads)
    ✓ Retention: HEAL_RETENTION_DAYS (default 31) purges older heals, calls, tries and cache entries; 0 keeps nothing,
      a heal's rows are removed the moment it finishes and nothing is cached, rolled up or counted
    ✓ Images: HEAL_OVERLAY_RETENTION_DAYS (default 7) deletes the bytes of an overlay and keeps the heal and a marker, so the API
      says "expired" and not "never there"; 0 stores no image. An image never outlives its record
    ✓ Rollup: a heal about to be purged is first counted into the `rollup` table (counts only, per day), in the same transaction
      that deletes it, so a failed purge loses nothing and a purge run twice counts nothing twice
    ✓ Size cap: HEAL_MAX_DB_MB (default 0 = off) expires the oldest images first, then purges the oldest records (rolled up
      first), those past the durability window before any younger one; purging a younger record is logged as a warning
    ✓ A fixed set of columns: update_heal refuses a name that is not one, so a typo cannot create a field
    ✓ A diagnosis (mode `diagnose`) keeps its cause as the reason, its confidence, whether an edit was suggested and why the model's
      answer was changed: the filters `confidence`, `patch_suggested` and `downgraded` list them; no evidence, note or edit text
    ✓ put_overlay() / get_overlay(): the marked screenshot of a heal's vision call (PNG bytes, at most one per heal); nothing is
      stored with retention 0, and it goes when its heal is purged
    ✓ run_tokens(): tokens spent by one run, the number the per-run budget is checked against
    ✓ list_heals(): the filtered list behind every aggregate (repo, framework, language, outcome, reason, step, tier
      movement, page language, test, file, locator, run, PR state, mode, model or role of a call, flags such as
      "a try hit the wrong element" or "broke again", time window), newest first or slowest first, paged. A filter on
      outcome, reason, PR state, tier movement or mode takes several values separated by commas
    ✓ followup(): what a client saw after the PR (merged unchanged, edited, reverted, still open, closed unmerged; whether
      the healed locator failed again; runs and days since) stored on the heal record
    ✓ A store written by an earlier version gains the columns and tables it lacks when it is opened, and keeps its rows
    ✓ The path ":memory:" gives a store that lives and dies with the process (offline replays, tests)

Environment Variables:
    None here (HEAL_DB, HEAL_RETENTION_DAYS, HEAL_OVERLAY_RETENTION_DAYS and HEAL_MAX_DB_MB are read by config.ServiceSettings).

Author: PMAC
===============================================================================
"""
import json
import logging
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .rollup import (BAD_PR_STATES, DAY, DIMENSIONS, DURABLE_DAYS, DURABLE_RUNS, Key, day_of, dims_of, heal_counts, merge,
                     scopes_of)

log = logging.getLogger("voyageur")
PURGE_EVERY_S = 60        # read routes purge expired rows at most this often
MAX_AGG_ROWS = 20000      # heals an aggregate reads (the newest); calls and tries are those of the same heals
_HEAL_COLUMNS = (
    "run_id", "repo", "framework", "language", "test_id", "file", "line", "page_path", "page_language",
    "broken_locator", "broken_kind", "broken_tier", "locator_key", "drift", "outcome", "reason", "why", "settled_at",
    "ended_at", "rounds", "candidates_tried", "wrong_element", "final_locator", "final_kind", "final_tier",
    "tier_movement", "below_floor", "branch", "covers", "cache_hit", "budget_exhausted", "verify_count", "verify_ref",
    "verify_passed", "duration_ms", "pr_state", "broke_again_after", "evidence", "finished_at", "mode", "failed_again",
    "runs_since", "days_since", "followup_at", "pr_states_seen", "confidence", "patch_suggested", "downgraded")
_INT = {"line", "broken_tier", "drift", "settled_at", "ended_at", "rounds", "candidates_tried", "wrong_element",
        "final_tier", "below_floor", "branch", "covers", "cache_hit", "budget_exhausted", "verify_count",
        "verify_passed", "duration_ms", "broke_again_after", "failed_again", "runs_since", "days_since",
        "patch_suggested"}
_REAL = {"finished_at", "followup_at"}
# filters that compare one column for equality
_EQ = ("repo", "framework", "language", "settled_at", "page_language", "test_id", "file", "run_id", "ended_at",
       "locator_key", "confidence")
# filters that take one value or several separated by commas ("edited,reverted")
_ONE_OF = ("outcome", "reason", "tier_movement", "pr_state", "mode")
# filters that are a yes or no (1, 0, true, false) on a column
_FLAGS = ("drift", "branch", "budget_exhausted", "cache_hit", "failed_again", "below_floor", "patch_suggested", "downgraded",
          "verify_passed")
# filters with their own meaning: wrong_element (a try hit the wrong element), with_calls (a model call was made),
# led_to_fix (healed and not edited, reverted or closed unmerged), model / role / local (a call matches), durability
_SPECIAL = ("wrong_element", "with_calls", "led_to_fix", "model", "role", "local", "durability")
_FILTERS = _EQ + _ONE_OF + _FLAGS + _SPECIAL
SORTS = ("newest", "slowest")
MAX_FILTER_VALUES = 20                                        # values one list filter may name
ROLLUP_BATCH = 500                                            # heals rolled up and deleted in one transaction
PROTECT_S = 3600                                              # the size cap never takes a heal younger than this
MB = 1024 * 1024
_NO_IMAGE = sqlite3.Binary(b"")                               # what an expired overlay keeps in place of its bytes

def _sql_type(column: str) -> str:
    return " INTEGER" if column in _INT else " REAL" if column in _REAL else " TEXT"


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS heals (
  heal_id TEXT PRIMARY KEY, created_at REAL NOT NULL,
  {', '.join(c + _sql_type(c) for c in _HEAL_COLUMNS)}
);
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, heal_id TEXT NOT NULL, created_at REAL NOT NULL, role TEXT, model TEXT,
  local INTEGER, input_tokens INTEGER, output_tokens INTEGER, images INTEGER, latency_ms INTEGER, ok INTEGER, step INTEGER);
CREATE TABLE IF NOT EXISTS tries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, heal_id TEXT NOT NULL, round INTEGER, ref TEXT, kind TEXT, code TEXT,
  result TEXT, detail TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS overlays (heal_id TEXT PRIMARY KEY, png BLOB NOT NULL, created_at REAL NOT NULL, expired_at REAL);
CREATE TABLE IF NOT EXISTS rollup (
  day INTEGER NOT NULL, scope TEXT NOT NULL, repo TEXT NOT NULL, framework TEXT NOT NULL, language TEXT NOT NULL,
  page_language TEXT NOT NULL, metric TEXT NOT NULL, k1 TEXT NOT NULL, k2 TEXT NOT NULL, k3 TEXT NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (day, scope, repo, framework, language, page_language, metric, k1, k2, k3)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS decisions (key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS calls_heal ON calls(heal_id);
CREATE INDEX IF NOT EXISTS tries_heal ON tries(heal_id);
CREATE INDEX IF NOT EXISTS heals_run ON heals(run_id);
CREATE INDEX IF NOT EXISTS heals_created ON heals(created_at);
CREATE INDEX IF NOT EXISTS overlays_created ON overlays(created_at);
"""


class FilterError(ValueError):
    """A filter value the store cannot use (a flag that is not 1 or 0, a sort it does not know)."""


class Store:
    """The audit store. Safe to share between threads."""

    def __init__(self, path: str = ":memory:", retention_days: int = 31, clock: Callable[[], float] = time.time,
                 overlay_retention_days: int = 7, max_db_mb: int = 0):
        self.retention_days, self.clock = retention_days, clock
        self.overlay_retention_days = overlay_retention_days
        self.max_db_bytes = max_db_mb * MB                      # 0: no cap
        self._last_purge = float("-inf")
        self._cap_stuck = False                                 # a cap nothing can meet is reported once, not on every purge
        self._known: Optional[Dict[str, set]] = None            # the names the rollup holds (rollup.dims_of), read once
        self._lock = threading.Lock()
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA auto_vacuum = INCREMENTAL")     # takes effect only on a file with no table yet
            self._db.executescript(_SCHEMA)
            have = {r["name"] for r in self._db.execute("PRAGMA table_info(heals)")}
            for column in _HEAL_COLUMNS:          # a store of an earlier version lacks the columns added since
                if column not in have:
                    self._db.execute(f"ALTER TABLE heals ADD COLUMN {column}{_sql_type(column)}")
            if "expired_at" not in {r["name"] for r in self._db.execute("PRAGMA table_info(overlays)")}:
                self._db.execute("ALTER TABLE overlays ADD COLUMN expired_at REAL")
            self._db.commit()
        if retention_days == 0:
            self._delete_heals("1 = 1", ())              # nothing is kept: leftovers of a crashed run go too

    def _run(self, sql: str, args: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, args)
            rows = cur.fetchall()
            self._db.commit()
            return rows

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- heals --------------------------------------------------------------------------------------------------
    def create_heal(self, heal_id: str, **fields: Any) -> None:
        self._run("INSERT INTO heals (heal_id, created_at) VALUES (?, ?)", (heal_id, self.clock()))
        if fields:
            self.update_heal(heal_id, **fields)

    def update_heal(self, heal_id: str, **fields: Any) -> None:
        bad = [k for k in fields if k not in _HEAL_COLUMNS]
        if bad:
            raise ValueError(f"unknown heal field(s): {', '.join(bad)}")
        if not fields:
            return
        values = [json.dumps(v) if isinstance(v, (dict, list)) else int(v) if isinstance(v, bool) else v
                  for v in fields.values()]
        self._run(f"UPDATE heals SET {', '.join(k + ' = ?' for k in fields)} WHERE heal_id = ?", (*values, heal_id))

    def followup(self, heal_id: str, pr_state: str, failed_again: bool, runs_since: int, days_since: int,
                 broke_again_after: Optional[int] = None) -> None:
        """What a client saw after the PR: its state, whether the healed locator failed again and how many runs and days have
        passed since. `broke_again_after` is the runs it took to break: the value given (kept from the first report), else
        `runs_since` when it has failed, None while it has not."""
        broke = (broke_again_after if broke_again_after is not None else runs_since) if failed_again else None
        self.update_heal(heal_id, pr_state=pr_state, failed_again=int(failed_again), runs_since=runs_since,
                         days_since=days_since, broke_again_after=broke, followup_at=self.clock())

    def bump_covers(self, heal_id: str) -> None:
        """One more test is covered by this heal's fix (a repeat of the same drift served from the decision cache)."""
        self._run("UPDATE heals SET covers = COALESCE(covers, 1) + 1 WHERE heal_id = ?", (heal_id,))

    def add_call(self, heal_id: str, *, role: str, model: str, local: bool, input_tokens: int, output_tokens: int,
                 images: int, latency_ms: int, ok: bool, step: int) -> None:
        self._run("INSERT INTO calls (heal_id, created_at, role, model, local, input_tokens, output_tokens, images, "
                  "latency_ms, ok, step) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (heal_id, self.clock(), role, model, int(local), input_tokens, output_tokens, images, latency_ms,
                   int(ok), step))

    def add_try(self, heal_id: str, round_: int, ref: str, kind: str, code: str, result: str, detail: str = "") -> None:
        self._run("INSERT INTO tries (heal_id, round, ref, kind, code, result, detail, created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (heal_id, round_, ref, kind, code, result, detail, self.clock()))

    def update_try(self, heal_id: str, round_: int, result: str, detail: str = "") -> None:
        self._run("UPDATE tries SET result = ?, detail = ? WHERE heal_id = ? AND round = ?",
                  (result, detail, heal_id, round_))

    def get_heal(self, heal_id: str) -> Optional[Dict[str, Any]]:
        self.purge_if_due()
        rows = self._run("SELECT * FROM heals WHERE heal_id = ?", (heal_id,))
        if not rows:
            return None
        rec = dict(rows[0])
        rec["calls"] = [dict(r) for r in self._run("SELECT * FROM calls WHERE heal_id = ? ORDER BY id", (heal_id,))]
        rec["tries"] = [dict(r) for r in self._run("SELECT * FROM tries WHERE heal_id = ? ORDER BY id", (heal_id,))]
        if isinstance(rec.get("evidence"), str):
            rec["evidence"] = json.loads(rec["evidence"])
        rec["overlay"] = self.overlay_state(heal_id)
        return rec

    def list_heals(self, since: Optional[float] = None, until: Optional[float] = None, limit: int = 100, offset: int = 0,
                   sort: str = "newest", **filters: Any) -> List[Dict[str, Any]]:
        """Heal rows (no calls or tries), newest first (or slowest first), matching every filter given (None means
        unfiltered)."""
        if sort not in SORTS:
            raise FilterError(f"sort must be one of {' | '.join(SORTS)}")
        self.purge_if_due()
        where, args = self._where(since, until, filters)
        order = "duration_ms DESC, created_at DESC, rowid DESC" if sort == "slowest" else "created_at DESC, rowid DESC"
        rows = self._run(f"SELECT * FROM heals {where} ORDER BY {order} LIMIT ? OFFSET ?",
                         (*args, max(0, min(limit, MAX_AGG_ROWS)), max(0, offset)))
        return [dict(r) for r in rows]

    def count_heals(self, since: Optional[float] = None, until: Optional[float] = None, **filters: Any) -> int:
        """How many heals match the filters (the size of the list behind a number, whatever the page size)."""
        self.purge_if_due()
        where, args = self._where(since, until, filters)
        return int(self._run(f"SELECT COUNT(*) AS n FROM heals {where}", args)[0]["n"])

    def heals_in(self, since: Optional[float] = None, until: Optional[float] = None, max_rows: int = MAX_AGG_ROWS,
                 **filters: Any) -> List[Dict[str, Any]]:
        """The newest `max_rows` heal rows matching the filters, oldest first: what an aggregate is computed over."""
        self.purge_if_due()
        where, args = self._where(since, until, filters)
        return [dict(r) for r in self._run(
            f"SELECT * FROM (SELECT * FROM heals {where} ORDER BY created_at DESC, rowid DESC LIMIT ?) "
            f"ORDER BY created_at", (*args, max(1, max_rows)))]

    def calls_in(self, since: Optional[float] = None, until: Optional[float] = None, max_rows: int = MAX_AGG_ROWS,
                 **filters: Any) -> List[Dict[str, Any]]:
        """Model-call rows of the newest `max_rows` heals matching the filters."""
        where, args = self._where(since, until, filters, "h2.")
        rows = self._run(
            "SELECT c.*, h.outcome AS outcome, h.settled_at AS heal_settled_at, h.pr_state AS pr_state, "
            "h.run_id AS run_id, COALESCE(h.mode, 'heal') AS mode FROM calls c "
            "JOIN heals h ON h.heal_id = c.heal_id WHERE c.heal_id IN "
            f"(SELECT h2.heal_id FROM heals h2 {where} ORDER BY h2.created_at DESC, h2.rowid DESC LIMIT ?)",
            (*args, max(1, max_rows)))
        return [dict(r) for r in rows]

    def tries_in(self, since: Optional[float] = None, until: Optional[float] = None, max_rows: int = MAX_AGG_ROWS,
                 **filters: Any) -> List[Dict[str, Any]]:
        """Try rows of the newest `max_rows` heals matching the filters."""
        where, args = self._where(since, until, filters, "h2.")
        return [dict(r) for r in self._run(
            "SELECT t.* FROM tries t WHERE t.heal_id IN "
            f"(SELECT h2.heal_id FROM heals h2 {where} ORDER BY h2.created_at DESC, h2.rowid DESC LIMIT ?)",
            (*args, max(1, max_rows)))]

    @staticmethod
    def _flag(key: str, value: Any) -> int:
        word = str(value).strip().lower()
        if word in ("1", "true", "yes"):
            return 1
        if word in ("0", "false", "no"):
            return 0
        raise FilterError(f"{key} must be 1 or 0")

    @classmethod
    def _where(cls, since: Optional[float], until: Optional[float], filters: Dict[str, Any], prefix: str = ""):
        """The WHERE clause for the filters. Every value is a bound parameter, and a column name comes only from the
        fixed lists above, never from the caller."""
        clauses: List[str] = []
        args: List[Any] = []
        p = prefix
        outer = prefix or "heals."                # the heal row a subquery on calls refers back to
        call_terms: List[str] = []
        call_args: List[Any] = []
        for key, value in filters.items():
            if key not in _FILTERS:
                raise ValueError(f"unknown filter {key}")
            if value is None:
                continue
            if key in _EQ:
                clauses.append(f"{p}{key} = ?")
                args.append(value)
            elif key in _ONE_OF:
                values = [v.strip() for v in str(value).split(",") if v.strip()]
                if not values:
                    continue
                if len(values) > MAX_FILTER_VALUES:
                    raise FilterError(f"{key} takes at most {MAX_FILTER_VALUES} values")
                col = f"COALESCE({p}mode, 'heal')" if key == "mode" else f"{p}{key}"
                parts = [f"{col} IN ({', '.join('?' * len(values))})"]
                if key in ("reason", "pr_state") and ("unknown" in values or "none" in values):
                    parts.append(f"{col} IS NULL")        # "unknown" / "none": the record holds nothing
                clauses.append("(" + " OR ".join(parts) + ")")
                args.extend(values)
            elif key in _FLAGS:
                want = cls._flag(key, value)
                clauses.append(f"COALESCE({p}{key}, 0) {'<>' if want else '='} 0")
            elif key == "wrong_element":
                clauses.append(f"COALESCE({p}wrong_element, 0) {'>' if cls._flag(key, value) else '='} 0")
            elif key == "with_calls":
                exists = f"EXISTS (SELECT 1 FROM calls c WHERE c.heal_id = {outer}heal_id)"
                clauses.append(exists if cls._flag(key, value) else f"NOT {exists}")
            elif key == "led_to_fix":
                good = (f"({p}outcome = 'healed' AND ({p}pr_state IS NULL OR {p}pr_state NOT IN "
                        f"({', '.join('?' * len(BAD_PR_STATES))})))")
                clauses.append(good if cls._flag(key, value) else f"NOT {good}")
                args.extend(BAD_PR_STATES)
            elif key in ("model", "role"):
                call_terms.append(f"c.{key} = ?")
                call_args.append(value)
            elif key == "local":
                call_terms.append('c."local" = ?')
                call_args.append(cls._flag(key, value))
            elif key == "durability":
                due = (f"{p}outcome = 'healed' AND {p}pr_state = 'merged_unchanged' AND {p}runs_since >= {DURABLE_RUNS} "
                       f"AND {p}days_since >= {DURABLE_DAYS}")
                if value == "pass":
                    clauses.append(f"({due} AND COALESCE({p}failed_again, 0) = 0)")
                elif value == "fail":
                    clauses.append(f"({p}outcome = 'healed' AND COALESCE({p}failed_again, 0) <> 0)")
                elif value == "pending":
                    clauses.append(f"({p}outcome = 'healed' AND {p}followup_at IS NOT NULL AND "
                                   f"COALESCE({p}failed_again, 0) = 0 AND NOT ({due}))")
                else:
                    raise FilterError("durability must be pass, fail or pending")
        if call_terms:
            clauses.append(f"EXISTS (SELECT 1 FROM calls c WHERE c.heal_id = {outer}heal_id AND {' AND '.join(call_terms)})")
            args.extend(call_args)
        if since is not None:
            clauses.append(f"{p}created_at >= ?")
            args.append(since)
        if until is not None:
            clauses.append(f"{p}created_at <= ?")
            args.append(until)
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", tuple(args)

    def run_tokens(self, run_id: str) -> int:
        """Input plus output tokens the model calls of one run have used."""
        rows = self._run("SELECT COALESCE(SUM(c.input_tokens + c.output_tokens), 0) AS n FROM calls c "
                         "JOIN heals h ON h.heal_id = c.heal_id WHERE h.run_id = ?", (run_id,))
        return int(rows[0]["n"])

    def count_rows(self, table: str) -> int:
        if table not in ("heals", "calls", "tries", "decisions", "overlays", "rollup"):
            raise ValueError("unknown table")
        return int(self._run(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])

    # ---- overlay (the marked screenshot a vision model was shown) -----------------------------------------------
    @property
    def image_days(self) -> int:
        """Days an image is kept: its own setting, and never longer than its record."""
        return min(self.overlay_retention_days, self.retention_days)

    def put_overlay(self, heal_id: str, png: bytes) -> None:
        """Keep the overlay of a heal's vision call. A no-op with retention 0 (nothing of a heal is kept) or an image
        period of 0."""
        if self.retention_days == 0 or self.overlay_retention_days == 0:
            return
        self._run("INSERT OR REPLACE INTO overlays (heal_id, png, created_at, expired_at) VALUES (?,?,?,NULL)",
                  (heal_id, sqlite3.Binary(png), self.clock()))

    def get_overlay(self, heal_id: str) -> Optional[bytes]:
        """The overlay PNG of a heal, or None (no vision call, or the image expired: overlay_state tells which)."""
        self.purge_if_due()
        rows = self._run("SELECT png FROM overlays WHERE heal_id = ? AND expired_at IS NULL", (heal_id,))
        return bytes(rows[0]["png"]) if rows else None

    def overlay_state(self, heal_id: str) -> Dict[str, Any]:
        """What the store holds of a heal's image: `kept` (with its size and when it will expire), `expired` (with when)
        or `none` (no vision call made one, or this store keeps no images)."""
        rows = self._run("SELECT created_at, expired_at, length(png) AS bytes FROM overlays WHERE heal_id = ?", (heal_id,))
        if not rows:
            return {"state": "none"}
        row = rows[0]
        if row["expired_at"] is not None:
            return {"state": "expired", "expired_at": row["expired_at"]}
        return {"state": "kept", "bytes": row["bytes"], "expires_at": row["created_at"] + self.image_days * DAY}

    # ---- decision cache -----------------------------------------------------------------------------------------
    def cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        if self.retention_days == 0:
            return None
        rows = self._run("SELECT payload, created_at FROM decisions WHERE key = ?", (key,))
        if not rows or rows[0]["created_at"] < self.clock() - self.retention_days * DAY:
            return None
        return json.loads(rows[0]["payload"])

    def cache_put(self, key: str, payload: Dict[str, Any]) -> None:
        if self.retention_days == 0:
            return
        self._run("INSERT OR REPLACE INTO decisions (key, payload, created_at) VALUES (?,?,?)",
                  (key, json.dumps(payload), self.clock()))

    # ---- retention ----------------------------------------------------------------------------------------------
    def _delete_ids(self, ids: List[str]) -> None:
        """Delete these heals and their rows. No lock and no commit: the caller holds the lock and ends the transaction."""
        marks = ", ".join("?" * len(ids))
        for table in ("calls", "tries", "overlays"):
            self._db.execute(f"DELETE FROM {table} WHERE heal_id IN ({marks})", ids)
        self._db.execute(f"DELETE FROM heals WHERE heal_id IN ({marks})", ids)

    def _delete_heals(self, where: str, args: tuple) -> None:
        """Delete heals without counting them: retention 0 keeps nothing, a rollup included."""
        self._run(f"DELETE FROM calls WHERE heal_id IN (SELECT heal_id FROM heals WHERE {where})", args)
        self._run(f"DELETE FROM tries WHERE heal_id IN (SELECT heal_id FROM heals WHERE {where})", args)
        self._run(f"DELETE FROM overlays WHERE heal_id IN (SELECT heal_id FROM heals WHERE {where})", args)
        self._run(f"DELETE FROM heals WHERE {where}", args)

    def _roll_up_and_delete(self, where: str, args: tuple, limit: int = ROLLUP_BATCH, once: bool = False) -> int:
        """Count the oldest heals matching `where` into the rollup and delete them, `limit` at a time (every match, or with
        `once` one batch), each batch one transaction: a failure leaves the heals in place and uncounted, never counted and
        still there. Returns how many went."""
        gone = 0
        while True:
            with self._lock:
                try:
                    self._db.execute("BEGIN IMMEDIATE")               # read and delete as one step, even with another process
                    heals = [dict(r) for r in self._db.execute(
                        f"SELECT * FROM heals WHERE {where} ORDER BY created_at, rowid LIMIT ?", (*args, max(1, limit)))]
                    if not heals:
                        self._db.rollback()
                        return gone
                    ids = [h["heal_id"] for h in heals]
                    marks = ", ".join("?" * len(ids))
                    calls: Dict[str, List[Dict[str, Any]]] = {i: [] for i in ids}
                    tries: Dict[str, List[Dict[str, Any]]] = {i: [] for i in ids}
                    for r in self._db.execute(f"SELECT * FROM calls WHERE heal_id IN ({marks})", ids):
                        calls[r["heal_id"]].append(dict(r))
                    for r in self._db.execute(f"SELECT * FROM tries WHERE heal_id IN ({marks})", ids):
                        tries[r["heal_id"]].append(dict(r))
                    if self._known is None:
                        self._known = {d: {r[0] for r in self._db.execute(f"SELECT DISTINCT {d} FROM rollup")} for d in DIMENSIONS}
                        self._known["combos"] = {tuple(r) for r in self._db.execute(
                            "SELECT DISTINCT repo, framework, language, page_language FROM rollup")}
                    known = self._known
                    rows: Dict[tuple, int] = {}
                    for h in heals:
                        counts = heal_counts(h, calls[h["heal_id"]], tries[h["heal_id"]])
                        dims = dims_of(h, known)
                        for scope in scopes_of(calls[h["heal_id"]]):
                            merge(rows, counts, (day_of(h["created_at"]), scope, *dims))
                    self._db.executemany(
                        "INSERT INTO rollup (day, scope, repo, framework, language, page_language, metric, k1, k2, k3, n) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (day, scope, repo, framework, language, page_language, "
                        "metric, k1, k2, k3) DO UPDATE SET n = n + excluded.n", [(*k, n) for k, n in rows.items()])
                    self._delete_ids(ids)
                    self._db.commit()
                except BaseException:
                    self._db.rollback()
                    self._known = None                              # re-read what the rollup really holds
                    raise
            gone += len(heals)
            if once:
                return gone

    def _expire_images(self, where: str, args: tuple) -> int:
        """Delete the bytes of the images matching `where`; the row stays, empty and marked, so the API can say the image
        expired. Returns how many."""
        with self._lock:
            cur = self._db.execute(f"UPDATE overlays SET png = ?, expired_at = ? WHERE expired_at IS NULL AND {where}",
                                   (_NO_IMAGE, self.clock(), *args))
            self._db.commit()
            return cur.rowcount

    def used_bytes(self) -> int:
        """Bytes of the database file in use: the pages that hold data, not the free pages a delete leaves behind (those are
        reused by the next insert, so a file stops growing at the cap even though it does not shrink on its own)."""
        with self._lock:
            pages = self._db.execute("PRAGMA page_count").fetchone()[0] - self._db.execute("PRAGMA freelist_count").fetchone()[0]
            return pages * self._db.execute("PRAGMA page_size").fetchone()[0]

    def purge(self) -> None:
        """Enforce retention. A record older than HEAL_RETENTION_DAYS is counted into the rollup and deleted; the bytes of an
        image older than HEAL_OVERLAY_RETENTION_DAYS are deleted and its heal stays; then the size cap, if set. With retention
        0 finish() already deletes each heal as it ends, and a purge must not remove one still in conversation, so there is
        nothing to do here. Running it again changes nothing."""
        if self.retention_days == 0:
            return
        try:
            now = self.clock()
            cutoff = now - self.retention_days * DAY
            self._roll_up_and_delete("created_at < ?", (cutoff,))
            self._expire_images("created_at < ?", (now - self.image_days * DAY,))
            self._run("DELETE FROM overlays WHERE created_at < ?", (cutoff,))
            self._run("DELETE FROM decisions WHERE created_at < ?", (cutoff,))
            if self.max_db_bytes:
                self._enforce_cap(now)
        except Exception:                      # a purge that cannot finish must not fail the request that triggered it
            log.exception("purge failed: nothing was lost, it is tried again on the next request")

    def _enforce_cap(self, now: float) -> None:
        """Get under HEAL_MAX_DB_MB: the oldest images first, then the oldest records (rolled up first), those past the
        durability window before any younger one. A younger record goes only when nothing else is left to free, and says so."""
        over = self.used_bytes() - self.max_db_bytes
        if over <= 0:
            self._cap_stuck = False
            return
        imaged = 0
        while over > 0:                                           # 1. images, oldest first, as many as the excess needs
            rows = self._run("SELECT heal_id, length(png) AS n FROM overlays WHERE expired_at IS NULL "
                             "ORDER BY created_at, heal_id LIMIT 200")
            if not rows:
                break
            take, freed = [], 0
            for r in rows:
                take.append(r["heal_id"])
                freed += r["n"]
                if freed >= over:
                    break
            self._expire_images(f"heal_id IN ({', '.join('?' * len(take))})", tuple(take))
            imaged += len(take)
            over = self.used_bytes() - self.max_db_bytes
        window = now - DURABLE_DAYS * DAY
        old = 0
        while over > 0:                                           # 2. records past the durability window, oldest first
            n = self._roll_up_and_delete("created_at < ?", (window,), max(1, min(ROLLUP_BATCH, over // 1500)), once=True)
            if not n:
                break
            old += n
            over = self.used_bytes() - self.max_db_bytes
        forced, size = 0, max(1, min(ROLLUP_BATCH, over // 1500))
        while over > 0:                                           # 3. the cap forces out a record that could still be measured
            before = over                                         # never one from the last hour (a conversation may still be
            n = self._roll_up_and_delete("created_at < ?", (now - PROTECT_S,), size, once=True)      # writing to it)
            if not n:
                break
            forced += n
            over = self.used_bytes() - self.max_db_bytes
            if over >= before:                                    # a batch freed no whole page: take a bigger one, and give up
                if size >= ROLLUP_BATCH:                          # when the biggest frees nothing, rather than empty the store
                    break
                size = min(ROLLUP_BATCH, size * 2)
        if imaged or old:
            log.info("HEAL_MAX_DB_MB (%g MB): expired %d images and purged %d records past the durability window.",
                     self.max_db_bytes / MB, imaged, old)
        if forced:
            log.warning("HEAL_MAX_DB_MB (%g MB) forced %d heal records younger than %d days out of the store; their counts "
                        "are kept, but durability (still passing %d days later) can no longer be measured for them. Raise "
                        "the cap or lower HEAL_OVERLAY_RETENTION_DAYS.", self.max_db_bytes / MB, forced, DURABLE_DAYS,
                        DURABLE_DAYS)
        if over > 0 and not self._cap_stuck:
            log.warning("HEAL_MAX_DB_MB (%g MB): the store cannot get under the cap, nothing is left to purge. Raise the cap.",
                        self.max_db_bytes / MB)
        self._cap_stuck = over > 0
        with self._lock:
            if self._db.execute("PRAGMA auto_vacuum").fetchone()[0] == 2:       # a file made by this version: give pages back,
                self._db.execute("PRAGMA incremental_vacuum(2000)")                # 8 MB at a time so a request is never held long
                self._db.commit()

    def purge_if_due(self) -> None:
        """purge(), at most once per PURGE_EVERY_S: every read route calls it, so rows expire even when no new heal
        starts."""
        now = self.clock()
        if now - self._last_purge >= PURGE_EVERY_S:
            self._last_purge = now
            self.purge()

    def finish(self, heal_id: str) -> None:
        """A heal reached its final answer: with retention 0 nothing of it is kept."""
        if self.retention_days == 0:
            self._delete_heals("heal_id = ?", (heal_id,))

    # ---- rollup (what purged heals left behind) -----------------------------------------------------------------
    def rollup_counts(self, since: Optional[float] = None, until: Optional[float] = None, **filters: Any) -> "Counter[Key]":
        """The counters of the purged heals that match the filters, summed. Only what a rollup row holds can be filtered on
        (repo, framework, language, page_language and model); a day is in the window when any of it is. A filter the rollup
        cannot answer (a run, an outcome, a file) matches nothing: rollup_answers() says so beforehand."""
        if not self.rollup_answers(filters):
            return Counter()
        where, args = ["scope = ?"], [filters.get("model") or ""]
        for d in DIMENSIONS:
            if filters.get(d) is not None:
                where.append(f"{d} = ?")
                args.append(filters[d])
        if since is not None:
            where.append("(day + 1) * ? > ?")
            args += [DAY, since]
        if until is not None:
            where.append("day * ? <= ?")
            args += [DAY, until]
        rows = self._run("SELECT metric, k1, k2, k3, SUM(n) AS n FROM rollup WHERE " + " AND ".join(where) +
                         " GROUP BY metric, k1, k2, k3", tuple(args))
        return Counter({(r["metric"], r["k1"], r["k2"], r["k3"]): r["n"] for r in rows})

    def rollup_by_dimension(self, dimension: str, since: Optional[float] = None, until: Optional[float] = None,
                            **filters: Any) -> Dict[str, int]:
        """Archived heals (every mode but advise and diagnose) by the value of one dimension, for the same filters as
        rollup_counts. A heal with no value for it is left out."""
        if dimension not in DIMENSIONS or not self.rollup_answers(filters):
            return {}
        where, args = ["scope = ?", "metric = 'heals'", f"{dimension} <> ''"], [filters.get("model") or ""]
        for d in DIMENSIONS:
            if filters.get(d) is not None:
                where.append(f"{d} = ?")
                args.append(filters[d])
        if since is not None:
            where.append("(day + 1) * ? > ?")
            args += [DAY, since]
        if until is not None:
            where.append("day * ? <= ?")
            args += [DAY, until]
        rows = self._run(f"SELECT {dimension} AS v, SUM(n) AS n FROM rollup WHERE " + " AND ".join(where) + f" GROUP BY {dimension}",
                         tuple(args))
        return {r["v"]: int(r["n"]) for r in rows}

    @staticmethod
    def rollup_answers(filters: Dict[str, Any]) -> bool:
        """Whether the rollup can answer for these filters: every one that is set is a dimension it keeps, or the model."""
        return all(v is None or k in DIMENSIONS or k == "model" for k, v in filters.items())

    def rollup_info(self) -> Dict[str, Any]:
        """How many heals the rollup holds and the span of days it covers (start of the first, end of the last), or Nones."""
        row = self._run("SELECT COALESCE(SUM(CASE WHEN metric = 'archived' THEN n END), 0) AS heals, MIN(day) AS first, "
                        "MAX(day) AS last FROM rollup WHERE scope = ''")[0]
        return {"heals": int(row["heals"]), "from": None if row["first"] is None else row["first"] * DAY,
                "through": None if row["last"] is None else (row["last"] + 1) * DAY}
