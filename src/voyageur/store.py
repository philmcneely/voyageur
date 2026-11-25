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
was shown), in a table of its own, so a reviewer can see what the model saw; it is purged with its heal and kept at all only when retention
is above 0. The decision cache lives here too: a repeat of the same drift (same broken call, same
page inventory, same language) costs no tokens.

Features:
    ✓ SQLite through the standard library, one connection behind a lock (the HTTP layer runs handlers in threads)
    ✓ Retention: HEAL_RETENTION_DAYS (default 14) purges older heals, calls, tries and cache entries; 0 keeps nothing,
      a heal's rows are removed the moment it finishes and nothing is cached
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
    ✓ A store written by an earlier version gains the columns it lacks when it is opened
    ✓ The path ":memory:" gives a store that lives and dies with the process (offline replays, tests)

Environment Variables:
    None here (HEAL_DB and HEAL_RETENTION_DAYS are read by config.ServiceSettings).

Author: PMAC
===============================================================================
"""
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DAY = 86400
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
_FLAGS = ("drift", "branch", "budget_exhausted", "cache_hit", "failed_again", "below_floor", "patch_suggested", "downgraded")
# filters with their own meaning: wrong_element (a try hit the wrong element), with_calls (a model call was made),
# led_to_fix (healed and not edited, reverted or closed unmerged), model / role / local (a call matches), durability
_SPECIAL = ("wrong_element", "with_calls", "led_to_fix", "model", "role", "local", "durability")
_FILTERS = _EQ + _ONE_OF + _FLAGS + _SPECIAL
BAD_PR_STATES = ("edited", "reverted", "closed_unmerged")      # a heal a human did not keep as written
DURABLE_RUNS, DURABLE_DAYS = 10, 30                           # "still passing 10 runs and 30 days later"
SORTS = ("newest", "slowest")
MAX_FILTER_VALUES = 20                                        # values one list filter may name
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
CREATE TABLE IF NOT EXISTS overlays (heal_id TEXT PRIMARY KEY, png BLOB NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS calls_heal ON calls(heal_id);
CREATE INDEX IF NOT EXISTS tries_heal ON tries(heal_id);
CREATE INDEX IF NOT EXISTS heals_run ON heals(run_id);
"""


class FilterError(ValueError):
    """A filter value the store cannot use (a flag that is not 1 or 0, a sort it does not know)."""


class Store:
    """The audit store. Safe to share between threads."""

    def __init__(self, path: str = ":memory:", retention_days: int = 14, clock: Callable[[], float] = time.time):
        self.retention_days, self.clock = retention_days, clock
        self._last_purge = float("-inf")
        self._lock = threading.Lock()
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            have = {r["name"] for r in self._db.execute("PRAGMA table_info(heals)")}
            for column in _HEAL_COLUMNS:          # a store of an earlier version lacks the columns added since
                if column not in have:
                    self._db.execute(f"ALTER TABLE heals ADD COLUMN {column}{_sql_type(column)}")
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
        if table not in ("heals", "calls", "tries", "decisions", "overlays"):
            raise ValueError("unknown table")
        return int(self._run(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])

    # ---- overlay (the marked screenshot a vision model was shown) -----------------------------------------------
    def put_overlay(self, heal_id: str, png: bytes) -> None:
        """Keep the overlay of a heal's vision call. A no-op with retention 0 (nothing of a heal is kept)."""
        if self.retention_days == 0:
            return
        self._run("INSERT OR REPLACE INTO overlays (heal_id, png, created_at) VALUES (?,?,?)",
                  (heal_id, sqlite3.Binary(png), self.clock()))

    def get_overlay(self, heal_id: str) -> Optional[bytes]:
        """The overlay PNG of a heal, or None (no vision call, or purged)."""
        self.purge_if_due()
        rows = self._run("SELECT png FROM overlays WHERE heal_id = ?", (heal_id,))
        return bytes(rows[0]["png"]) if rows else None

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
    def _delete_heals(self, where: str, args: tuple) -> None:
        self._run(f"DELETE FROM calls WHERE heal_id IN (SELECT heal_id FROM heals WHERE {where})", args)
        self._run(f"DELETE FROM tries WHERE heal_id IN (SELECT heal_id FROM heals WHERE {where})", args)
        self._run(f"DELETE FROM overlays WHERE heal_id IN (SELECT heal_id FROM heals WHERE {where})", args)
        self._run(f"DELETE FROM heals WHERE {where}", args)

    def purge(self) -> None:
        """Delete everything older than the retention period. With retention 0 finish() already deletes each heal as it
        ends, and a purge must not remove one still in conversation, so there is nothing to do here."""
        if self.retention_days == 0:
            return
        cutoff = self.clock() - self.retention_days * DAY
        self._delete_heals("created_at < ?", (cutoff,))
        self._run("DELETE FROM overlays WHERE created_at < ?", (cutoff,))
        self._run("DELETE FROM decisions WHERE created_at < ?", (cutoff,))

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
