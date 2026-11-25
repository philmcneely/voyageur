"""
===============================================================================
Locator Audit (how stable is every locator in these files, on this page?)
===============================================================================
POST /v1/audit and the `audit_locators` MCP tool. Given a page inventory and source files, report for each
page.locator / getBy* call in the files what a heal would ask of it: its tier, whether it points at exactly one listed
element, and whether a locator of a higher tier is unique for that same element, with the code the writer would produce.
No model is called and nothing is written: it is the same evidence and the same ranking a heal uses, run over code that
has not broken yet, so a team can fix a brittle locator before the page changes under it.

    kind / tier   locator_policy.kind_of and RANK (testid 0 ... text 7, chain 8; lower is more stable)
    matches       how many listed elements the call matches (None when the inventory cannot tell)
    status        ok        one element and no more stable locator is unique for it
                  upgrade   one element and a more stable locator is unique for it (`better`)
                  ambiguous the call matches more than one listed element
                  positional `.first` / `.last` / `.nth(n)` picks one element out of several the call matches: it works
                            today, but an element added before the target is picked with no error, a silent wrong element
                            no heal can see (`positional_of` = how many it picks from; `better` = a stable locator for it)
                  missing   the call matches no listed element
                  unknown   the inventory cannot say (a scoped chain, a regex, a frame, an element the listing does not show)

Features:
    ✓ Every locator call in every file, with its file, line and character span, in the file's own language (by extension);
      a `.first` / `.last` / `.nth(n)` after the call narrows it to one element and is read as part of it
    ✓ `better` is the first locator write() produces for the element that sits above the call's tier: the same candidates,
      uniqueness proof and validator a heal uses, so an audit never suggests what a heal would refuse to write
    ✓ A summary of counts by status and by kind, and `upgradeable`, the number of calls with a better locator (an upgrade or a positional call with a stable one)
    ✓ Bounded: at most MAX_LOCATORS calls reported; a call over MAX_AUDIT_EXPR characters is `unknown` (not evaluated); the
      audit stops at its own work budget (HEAL_AUDIT_BUDGET_S) and answers with the rows done and `truncated` true, never an
      error; a hostile file is skipped and named in `skipped_files`
    ✓ Source is only ever scanned as text, never imported or run

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from .failing import Call, iter_calls
from .inventory import Inventory
from .locator_policy import COPY_KINDS, RANK, kind_of, write
from .locators import receiver_of
from .schemas import AuditRequest

MAX_LOCATORS = 500
MAX_AUDIT_EXPR = 1000      # a call longer than this is reported unknown without being evaluated (a heal allows more)
_TS_EXT = (".ts", ".tsx", ".js", ".mjs", ".cjs")
_KIND_NAME = {"testid": "test_id"}
# what narrows a call to one element after it: Python's `.first` / `.last` properties are not part of the scanned call
_NARROW = re.compile(r"(?:[ \t]*\.(?:first|last)\b(?:\(\))?|[ \t]*\.nth\(\s*\d{1,6}\s*\)){1,4}")


def _language(path: str, default: str) -> str:
    return "typescript" if path.endswith(_TS_EXT) else "python" if path.endswith(".py") else default


def _hits(inv: Inventory, call: str) -> Optional[List[Dict[str, Any]]]:
    """The listed elements the call matches, or None when the inventory cannot tell. matched() names them; count() alone
    gives a number (an empty dict stands for an element it cannot name). When both answer and disagree nothing is claimed."""
    matched, n = inv.matched(call), inv.count(call)
    if matched is not None and n is not None and len(matched) != n:
        return None
    if matched is not None:
        return matched
    return None if n is None else [{}] * n


def _row(path: str, content: str, call: Call, inv: Inventory, language: str, test_id_attribute: str) -> Dict[str, Any]:
    kind = kind_of(call.text)
    narrow = _NARROW.match(content, call.end)
    row: Dict[str, Any] = {"file": path, "line": call.line, "span": {"start": call.start, "end": call.end},
                           "call": call.text[:300], "kind": _KIND_NAME.get(kind, kind), "tier": RANK[kind],
                           "narrowed": narrow.group(0).strip() if narrow else "", "copy_based": kind in COPY_KINDS, "matches": None, "unique": None, "status": "unknown",
                           "target_ref": None, "better": None}
    if len(call.text) > MAX_AUDIT_EXPR:
        row["why"] = f"the call is over {MAX_AUDIT_EXPR} characters, so an audit does not evaluate it"
        return row
    hits = _hits(inv, call.text + (narrow.group(0).strip() if narrow else ""))
    if hits is None:
        row["why"] = "the inventory cannot say what this call matches (a scoped chain, a regex or a frame)"
        return row
    row["matches"], row["unique"] = len(hits), len(hits) == 1
    if not hits:
        row.update(status="missing", why="the call matches no element on the page")
        return row
    if len(hits) > 1:
        row.update(status="ambiguous", why=f"the call matches {len(hits)} listed elements (strict mode would refuse it)")
        return row
    target = hits[0]
    if not target.get("ref"):
        row["why"] = "the call matches one element the listing does not show"
        return row
    row["target_ref"] = target["ref"]
    written = write(target, inv, language, receiver_of(call.text), kind, test_id_attribute, broken_call=call.text)
    base = _hits(inv, call.text) if narrow else None
    if base is not None and len(base) > 1:
        # a stable locator of any tier beats picking by position: the position is what can drift without an error
        row.update(status="positional", positional_of=len(base),
                   why=f"{row['narrowed']} picks by position among {len(base)} matches: an element added before the target "
                       "would be picked instead, a silent wrong element with no error for a heal to see")
        if written.candidates:
            row["better"] = _better(written.candidates[0], language)
        return row
    higher = [w for w in written.candidates if w.tier < RANK[kind]]
    if not higher:
        row.update(status="ok", why="one element, and no more stable locator is unique for it")
        return row
    best = higher[0]
    row["status"] = "upgrade"
    row["better"] = _better(best, language)
    row["why"] = f"a {row['better']['kind']} locator is unique for the same element and sits above this {row['kind']} one"
    return row


def _better(w: Any, language: str) -> Dict[str, Any]:
    """The `better` field for a written candidate: its kind, tier, whether it is copy, the code and the neutral form."""
    name = _KIND_NAME.get(w.kind, w.kind)
    return {"kind": name, "tier": w.tier, "copy_based": w.copy_based, "code": w.code[language],
            "locator": {"kind": name, "chain": w.neutral}}


def _result(req: AuditRequest, rows: List[Dict[str, Any]], skipped: List[str], truncated: bool, complete: bool) -> Dict[str, Any]:
    by_status = Counter(r["status"] for r in rows)
    return {"locators": rows, "truncated": truncated, "skipped_files": skipped,
            "summary": {"files": len(req.source.files), "locators": len(rows), "upgradeable": sum(1 for r in rows if r.get("better")),
                        "by_status": dict(by_status), "by_kind": dict(Counter(r["kind"] for r in rows)),
                        "inventory": len(req.page.inventory), "inventory_complete": complete}}


def empty_result(req: AuditRequest) -> Dict[str, Any]:
    """The answer when the worker was stopped before it returned anything: no rows, truncated."""
    return _result(req, [], [], True, req.page.inventory_more == 0 and bool(req.page.inventory))


def run_audit(req: AuditRequest, budget_s: Optional[float] = None) -> Dict[str, Any]:
    """Audit every locator call in the request's files against its page inventory, stopping at `budget_s` seconds of work
    (`truncated` true, the rows done returned). A module-level function: it can run in a worker process."""
    started = time.monotonic()
    inv = Inventory(req.page.flat_inventory(), req.page.inventory_more == 0, req.page.aria_snapshot or "",
                    req.page.inventory_copy_more, req.page.inventory_copy_withheld, req.page.inventory_copy_withheld_data)
    default = req.client.language if req.client else "python"
    rows: List[Dict[str, Any]] = []
    skipped: List[str] = []
    truncated = False
    for f in req.source.files:
        try:
            calls = iter_calls(f.content)
        except (RecursionError, MemoryError, ValueError):
            skipped.append(f.path)                           # a hostile file is skipped, never fatal
            continue
        language = _language(f.path, default)
        for call in calls:
            if len(rows) >= MAX_LOCATORS or (budget_s is not None and time.monotonic() - started > budget_s):
                truncated = True
                break
            rows.append(_row(f.path, f.content, call, inv, language, req.test_id_attribute))
        if truncated:
            break
    return _result(req, rows, skipped, truncated, inv.complete)
