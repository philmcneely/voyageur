"""
===============================================================================
CSV Export (the heal list as a spreadsheet, safe to open in one)
===============================================================================
GET /v1/heals?format=csv writes the same filtered list the JSON route returns, one row per heal, one column per field of the
record (the evidence summary is left out: it is a nested object). The cells hold text from records (a locator, a test id, a
reason sentence, a page path), and a spreadsheet treats a cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return
as a formula. Every text cell that starts with one of them is written with a leading single quote, so opening the file never
runs anything. Numbers are written as numbers.

Features:
    ✓ csv_cell(): a value as a CSV-safe cell (formula prefixes defused; None is empty; booleans as 1 and 0)
    ✓ heals_csv(): header and rows in a fixed column order, times as ISO 8601 UTC, quoted by the csv module (commas, quotes
      and newlines inside a cell are handled by it)
    ✓ Leading whitespace of any kind (spaces, newline, vertical tab, NBSP, zero-width characters) is skipped before the prefix is
      checked, so " =1+1" and "\n=1+1" are defused as well
    ✓ csv_chunks(): the export is written in pages read one at a time, so the store lock is released between pages and a big
      export never holds it

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import csv
import io
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
# whitespace and invisible characters a spreadsheet may skip before it reads a formula: every Unicode space (NBSP, line and
# vertical tabs, newline ...) and the zero-width ones
_BLANKS = "".join(chr(c) for c in range(0x110000) if chr(c).isspace()) + "\ufeff\u200b\u200c\u200d\u2060"
COLUMNS = ("heal_id", "created_at", "finished_at", "mode", "run_id", "repo", "framework", "language", "test_id", "file",
           "line", "page_path", "page_language", "broken_locator", "broken_kind", "broken_tier", "drift", "outcome",
           "reason", "why", "settled_at", "ended_at", "rounds", "candidates_tried", "wrong_element", "final_locator",
           "final_kind", "final_tier", "tier_movement", "below_floor", "branch", "covers", "cache_hit",
           "budget_exhausted", "verify_count", "verify_ref", "verify_passed", "duration_ms", "pr_state", "failed_again",
           "runs_since", "days_since", "broke_again_after", "followup_at", "locator_key")
_TIMES = ("created_at", "finished_at", "followup_at")


def csv_cell(value: Any) -> Any:
    """`value` as a cell: text starting with a formula character gets a leading single quote."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text.startswith(("\t", "\r")) or text.lstrip(_BLANKS).startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def _time(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _row_cells(row: Dict[str, Any]) -> List[Any]:
    cells: List[Any] = []
    for column in COLUMNS:
        value = row.get(column)
        if column == "mode":
            value = value or "heal"
        cells.append(_time(value) if column in _TIMES and value is not None else csv_cell(value))
    return cells


def csv_chunks(page: Callable[[int, int], List[Dict[str, Any]]], limit: int, size: int = 1000) -> Iterator[str]:
    """The CSV text in chunks: the header, then `page(at, n)` (n rows from row `at`) until `limit` rows or no more."""
    head = io.StringIO()
    csv.writer(head, lineterminator="\r\n").writerow(COLUMNS)
    yield head.getvalue()
    done = 0
    while done < limit:
        asked = min(size, limit - done)
        rows = page(done, asked)
        if not rows:
            return
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        for row in rows:
            writer.writerow(_row_cells(row))
        yield out.getvalue()
        done += len(rows)
        if len(rows) < asked:
            return


def heals_csv(rows: Iterable[Dict[str, Any]]) -> str:
    """The CSV text for heal rows (dicts from the store)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for row in rows:
        cells: List[Any] = []
        for column in COLUMNS:
            value = row.get(column)
            if column == "mode":
                value = value or "heal"
            cells.append(_time(value) if column in _TIMES and value is not None else csv_cell(value))
        writer.writerow(cells)
    return out.getvalue()
