"""
===============================================================================
Gates (what must hold before a pick is used, and before a heal counts)
===============================================================================
Two small checks that keep a plausible-looking answer from becoming a wrong heal.

verify_pick judges the element a pick (or a fingerprint match) chose, before any locator is written for it: it must be on the
list the model was shown, visible, something the test's action can be taken on, and not provably a different element than
the one the passing run used. "Provably different" is narrow on purpose: a renamed id on the picked element is the drift
being healed, so it is no reason to refuse; the old id or test id now belonging to another element, or the pick sitting in a
different row than the recorded one, is.

judge_result judges what the client reports after trying a candidate in the live page and re-running the exact test. Only
a count of exactly 1, the hit being the expected element and the test having passed is "ok", the one verdict that lets a heal
be recorded as healed. Anything else says which way it failed, so the caller can move to the next locator for the same
element (count, unverified, rerun_failed) or back to the pick (wrong_element).

Two rules compare the chosen element with what the passing run recorded (the fingerprint, and the other elements recorded
on the same page), both lifted from recorded runs where a wrong element got through:
    renamed_neighbour  a removed button is not "healed" onto its renamed neighbour: the chosen element is the same kind as the
        fingerprint element and has the size of ANOTHER recorded element of that kind but not the step's size, and neither the
        step's element nor that neighbour is still on the page with its old copy. A rename keeps an element's size; a removal
        shifts a neighbour into the freed place
    moved_into_frame   a page-level element is not the step's element when that element now sits in a frame: the chosen element
        is a page-level twin (same kind and copy) of it, a twin exists inside a frame, and no page-level twin is where the step's
        element was. The reason says to locate the frame first
The two rules above treat anything less as passing and never refuse on a missing fact. The identity proof (proof.py) is the
opposite: a pick that nothing proves is refused (no_identity_proof), and one whose hook attribute differs while its copy
differs too is another element (different_element).

Features:
    ✓ verify_pick(): not_on_list, not_visible, action_incompatible, different_element, renamed_neighbour, moved_into_frame,
      no_identity_proof (see proof.py: a pick needs positive proof), or None when the pick passes; a refusal is a str equal to its code with the sentence for the decision in `.why`
    ✓ verify_pick(kept=...): after a healed frame hop the step's own locator is one more proof (proof.prove_from_kept)
    ✓ a pick in another row than the recorded one is refused unless the row was renamed, not the pick moved: a kept unique hook,
      a kept data value, a widget renumbered as a whole, or a generated row id (React useId) regenerated, in the same place with
      the recorded copy and no other row holding it
    ✓ refusal_for(): the two recorded-case rules alone, for the fingerprint match to apply to its own choice
    ✓ judge_result(): ok, count, unverified, wrong_element, rerun_failed
    ✓ Pure functions over plain data: no model, no network

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .element_facts import copy_of, element_kind
from .failing import is_missing_locator, printed_expr, without_code_frame
from .locators import selectors_from
from .elements import _holder_elsewhere, box_of, compatible, describe_control, norm_text, same_place, stable_keys
from .locator_policy import is_generated_id
from .proof import prove

SIZE_TOLERANCE = 1.0            # px: an element keeps its own size when only its copy changes


class Refusal(str):
    """A refusal code that also carries the sentence for the decision (`why`). It equals its code as a plain str does."""
    why: str = ""

    def __new__(cls, code: str, why: str = "") -> "Refusal":
        obj = super().__new__(cls, code)
        obj.why = why
        return obj


def _kind(e: Dict[str, Any]) -> Dict[str, Any]:
    return element_kind({k: v for k, v in e.items() if k not in ("ref", "top_at_center", "hit")})


def _same_size(a: Any, b: Any) -> bool:
    ba, bb = box_of(a), box_of(b)
    return (ba is not None and bb is not None
            and abs(ba[2] - bb[2]) <= SIZE_TOLERANCE and abs(ba[3] - bb[3]) <= SIZE_TOLERANCE)


def _renamed_neighbour(chosen: Dict[str, Any], elements: Sequence[Dict[str, Any]], fp: Dict[str, Any],
                       neighbours: Sequence[Dict[str, Any]]) -> Optional[Refusal]:
    if box_of(fp.get("box")) is None or box_of(chosen.get("box")) is None:
        return None
    kind = _kind(chosen)
    if _kind(fp) != kind or _same_size(chosen.get("box"), fp.get("box")):
        return None
    now = [c for c in elements if not c.get("frame") and _kind(c) == kind]
    if any(copy_of(c) == copy_of(fp) for c in now):
        return None                                        # the step's element is still on the page
    for base in neighbours:
        if base.get("frame") or _kind(base) != kind or copy_of(base) == copy_of(fp):
            continue
        if _same_size(base.get("box"), chosen.get("box")) and not any(copy_of(c) == copy_of(base) for c in now):
            return Refusal("renamed_neighbour",
                           f"{describe_control(chosen)} looks like the renamed {describe_control(base)} that was on the "
                           "page when the test passed (the same kind of element and size, and nothing else is left of it), "
                           f"not this step's; {describe_control(fp)} was removed or replaced")
    return None


def _moved_into_frame(chosen: Dict[str, Any], elements: Sequence[Dict[str, Any]], framed: Sequence[Dict[str, Any]],
                      fp: Dict[str, Any]) -> Optional[Refusal]:
    kind, said, old = _kind(fp), copy_of(fp), box_of(fp.get("box"))
    if old is None:
        return None

    def twins(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [i for i in items if _kind(i) == kind and copy_of(i) == said]
    page, inside = twins([e for e in elements if not e.get("frame")]), twins(framed)
    if not inside or not any(chosen is t for t in page):
        return None
    if any((b := box_of(t.get("box"))) is not None and same_place(b, old) for t in page):
        return None                                        # the step's element is still where it was, at page level
    return Refusal("moved_into_frame",
                   f"{describe_control(chosen)} is on the page itself, but the element the step acted on "
                   f"({describe_control(fp)}) is now inside a frame ({str(inside[0].get('frame'))[:80]}): a page-level "
                   "locator never enters a frame. Locate the frame first (frame_locator / frameLocator), then the "
                   "element in it")


def refusal_for(chosen: Dict[str, Any], elements: Sequence[Dict[str, Any]], fingerprint: Optional[Dict[str, Any]],
                neighbours: Sequence[Dict[str, Any]] = (), framed: Sequence[Dict[str, Any]] = ()) -> Optional[Refusal]:
    """renamed_neighbour or moved_into_frame for `chosen` against the passing run's record, or None."""
    if not fingerprint:
        return None
    return _moved_into_frame(chosen, elements, framed, fingerprint) or _renamed_neighbour(
        chosen, elements, fingerprint, neighbours)


def verify_pick(element: Dict[str, Any], shown: Set[str], elements: List[Dict[str, Any]],
                fingerprint: Optional[Dict[str, Any]], action: Optional[str],
                neighbours: Sequence[Dict[str, Any]] = (), framed: Sequence[Dict[str, Any]] = (),
                call: str = "", language_changed: bool = False, kept: str = "", complete: bool = False,
                copy_short: bool = False) -> Optional[str]:
    """The reason code `element` may not be the target, or None when it may. `shown` is the refs the model was given;
    `neighbours` are the other elements the passing run recorded on the page, `framed` the elements now inside frames and
    `call` the broken locator (the facts a pick must be proven against when there is no fingerprint). `kept` is the part of the
    call after a frame hop that was healed, which the pick must still satisfy (the selector proof, proof.py), and `complete`
    whether the listing holds every element of the page, copy-holding ones included (False unless the caller knows: the wrapper
    proof, the selector proof and the regenerated-row rule then stay off). `copy_short`: the probe withheld short copy (inside an
    option or a data widget), so "no other row holds this copy" cannot be said: the regenerated-row rule stays off. A withheld
    element is never a control (it has no hook, role or click handler, or it would be listed), so the wrapper and selector proofs,
    which are about controls, do not need it."""
    if element.get("ref") not in shown:
        return "not_on_list"
    if element.get("hidden"):
        return "not_visible"
    if action and not compatible(action, element):
        return "action_incompatible"
    if fingerprint and _provably_another(fingerprint, element, [e for e in elements if e is not element],
                                           complete and not copy_short):
        return "different_element"
    refused = refusal_for(element, elements, fingerprint, neighbours, framed)
    if refused:
        return refused
    proof, other = prove(element, fingerprint, call, [e for e in elements if not e.get("hidden")], neighbours,
                         language_changed, kept, complete)
    if other:
        return Refusal("different_element", f"{describe_control(element)} is another element than the step's: {other}")
    if proof is None:
        return Refusal("no_identity_proof",
                       f"nothing shows {describe_control(element)} is the element the step meant: no stable attribute, copy "
                       "or place of it matches what the step or the passing run recorded")
    return None


def _provably_another(then: Dict[str, Any], now: Dict[str, Any], others: List[Dict[str, Any]],
                      complete: bool = False) -> bool:
    """True when a stable attribute of the recorded element now belongs to exactly one other element, the pick is in a
    different row, or the pick IS the row the recorded element sat in (its hook is the recorded row: a wrapper can share the
    element's box exactly, so place cannot tell them apart). (elements.proof_of also reports an attribute that merely
    changed; that is the drift, not a proof.)"""
    if then.get("row") and any(now.get(k) == then["row"] for k in ("testid", "data_qa", "data_cy", "data_test", "id")):
        return True
    for k in stable_keys(then, now):
        if then.get(k) and now.get(k) != then[k] and _holder_elsewhere(k, then[k], others, now.get("box")) is not None:
            return True
    if not (then.get("row") and now.get("row") and then["row"] != now["row"]):
        return False
    return not _row_renamed(then, now, others, complete)


_UNIQUE_HOOKS = ("testid", "data_qa", "data_cy", "data_test")


def _row_renamed(then: Dict[str, Any], now: Dict[str, Any], others: List[Dict[str, Any]], complete: bool = False) -> bool:
    """The row container was renamed, not the pick moved to another row: the recorded row is on no listed element any more,
    and the pick still carries the recorded element's own test hook, which no other element has. (A removed order row's
    menu button shares its hook with every other row's, so a neighbour row's button never passes this.)"""
    if any(o.get("row") == then["row"] or o.get("testid") == then["row"] for o in others):
        return False
    # Each exception below says "renamed, not moved": the pick must also be where the recorded element was. A removed row's
    # lone surviving neighbour can carry a hook that is unique only because it is the last row (security review 2026-11-07).
    a, b = box_of(now.get("box")), box_of(then.get("box"))
    if a is None or b is None or not same_place(a, b):
        return False
    if any(then.get(k) and now.get(k) == then[k] and not any(o.get(k) == then[k] for o in others) for k in _UNIQUE_HOOKS):
        return True
    if _kept_own_data_value(then, now, others):
        return True
    return _renumbered_together(then, now, others) or _regenerated_row(then, now, others, complete)


def _regenerated_row(then: Dict[str, Any], now: Dict[str, Any], others: List[Dict[str, Any]], complete: bool) -> bool:
    """The row's id is generated both before and now (React useId, `accordion-item-:r2u:` -> `:r5k:`), so a new value is the
    rebuild and no proof of another row. The pick must still be where the recorded element was (checked here too, and a
    missing box fails), carry the recorded copy, and no other element in a row of its own may carry that copy. "No other
    element" is only said of a listing that holds every element (`complete`: nothing left out, no copy-holding element
    left out either): an unlisted twin in another row would be missed."""
    if not complete:
        return False
    a, b = box_of(now.get("box")), box_of(then.get("box"))
    if a is None or b is None or not same_place(a, b):
        return False                                    # no box to compare, or not where it was: copy alone proves nothing
    copy = norm_text(then.get("name") or then.get("text"))
    if not copy or norm_text(now.get("name") or now.get("text")) != copy:
        return False
    if not (is_generated_id(str(then["row"])) and is_generated_id(str(now.get("row") or ""))):
        return False
    return not any(norm_text(o.get("name") or o.get("text")) == copy and o.get("row") and o["row"] != now["row"] for o in others)


def _kept_own_data_value(then: Dict[str, Any], now: Dict[str, Any], others: List[Dict[str, Any]]) -> bool:
    """Another data-* value of the recorded element that the pick still carries, no other element has, and that names
    something (a letter in it, three characters or more: a re-indexed data-index="2" names nothing), with the copy unchanged."""
    copy = norm_text(then.get("name") or then.get("text"))
    if not copy or norm_text(now.get("name") or now.get("text")) != copy:
        return False
    for k, v in then.items():
        if not (k.startswith("data_") and k not in _UNIQUE_HOOKS and isinstance(v, str)):
            continue
        if len(v) >= 3 and any(c.isalpha() for c in v) and now.get(k) == v and not any(o.get(k) == v for o in others):
            return True
    return False


def _renumbered_together(then: Dict[str, Any], now: Dict[str, Any], others: List[Dict[str, Any]]) -> bool:
    """A widget renumbered as a whole (react-select-3-listbox / -3-option-6 -> -4-listbox / -4-option-6): the row and the
    element's id changed by the same one number, the copy is the recorded copy, and no other element fits that."""
    shift = _number_shift(str(then["row"]), str(now["row"]))
    copy = norm_text(then.get("name") or then.get("text"))
    if shift is None or not copy or norm_text(now.get("name") or now.get("text")) != copy:
        return False
    if not (then.get("id") and _number_shift(str(then["id"]), str(now.get("id") or "")) == shift):
        return False
    return not any(norm_text(o.get("name") or o.get("text")) == copy and o.get("row")
                   and _number_shift(str(then["row"]), str(o["row"])) is not None for o in others)


_RUNS = re.compile(r"\d+|\D+")


def _number_shift(a: str, b: str) -> Optional[tuple]:
    """(old, new) when `a` and `b` are the same but for one number, else None."""
    ra, rb = _RUNS.findall(a), _RUNS.findall(b)
    if len(ra) != len(rb):
        return None
    diff = [(x, y) for x, y in zip(ra, rb) if x != y]
    return diff[0] if len(diff) == 1 and diff[0][0].isdigit() and diff[0][1].isdigit() else None


def judge_result(expected_ref: str, result: Any, healed: Optional[str] = None,
                 healed_at: Optional[Tuple[str, int]] = None, own_check: Optional[int] = None,
                 stack: Sequence[Any] = ()) -> str:
    """ok | ok_later | count | unverified | wrong_element | rerun_failed for a reported result (schemas.ResultRequest).
    ok_later: the re-run stopped provably AFTER the healed statement (its stack, outermost frame first, reaches a later line than
    the original failure's `stack`), and either at a different locator that found nothing (the next round can heal it) or past
    the healed statement's own `expect(` check, which ends at line `own_check` of the healed file (the statement and its check
    both held). `healed` is the written locator and `healed_at` its (file, line); without them, or without both stacks, nothing
    is ok_later."""
    if result.count != 1:
        return "count"
    if result.hit is None:
        return "unverified"
    if result.hit.ref != expected_ref:
        return "wrong_element"
    if result.test_outcome == "passed":
        return "ok"
    if result.test_outcome == "failed_later" and healed and healed_at and _later_elsewhere(result.later, healed, healed_at,
                                                                                          own_check, stack):
        return "ok_later"
    return "rerun_failed"


def _later_elsewhere(later: Any, healed: str, healed_at: Tuple[str, int], own_check: Optional[int], stack: Sequence[Any]) -> bool:
    """The later failure comes after the original one, the healed line is not on its stack, and it is past the healed
    statement's own check or a missing locator that is not the healed one."""
    frames = [(f.file, f.line) for f in later.stack]
    if not _after([(f.file, f.line) for f in stack], frames):
        return False
    if any(_same_file(f, healed_at[0]) and n == healed_at[1] for f, n in frames):
        return False
    if own_check and _same_file(frames[0][0], healed_at[0]) and frames[0][1] > own_check:
        return True
    if not is_missing_locator(later.error):
        return False
    flat, error = _unquote(healed), without_code_frame(later.error)
    named = [_unquote(x) for x in [printed_expr(error), *selectors_from(error)] if x]
    return not any(n in flat for n in named)


def _after(first: Sequence[Tuple[str, int]], then: Sequence[Tuple[str, int]]) -> bool:
    """`then` (frames innermost first) stopped at a later place than `first`: walking both from the outermost frame, the first
    frame that differs is in the same file and on a later line. A different file at the same depth, or the same place, is not
    provably later."""
    for (fa, la), (fb, lb) in zip(reversed(first), reversed(then)):
        if not _same_file(fa, fb):
            return False
        if la != lb:
            return lb > la
    return False


def _same_file(a: str, b: str) -> bool:
    return a.replace("\\", "/").removeprefix("./") == b.replace("\\", "/").removeprefix("./")


def own_check_line(source: str, end_line: int) -> Optional[int]:
    """The LAST line of the `expect(` statement that checks the statement ending at `end_line` (Playwright reports a failed
    check on its matcher's line, which a formatter may put lines below the `expect(`): the first statement after it, skipping
    blank and comment lines. None when another statement comes first, nothing follows, or the check never closes. Lines are
    split on `\n` only, as the line numbers of a stack are counted."""
    lines = source.split("\n")
    for n in range(end_line + 1, len(lines) + 1):
        text = lines[n - 1].strip()
        if text.startswith(("expect(", "await expect(")):
            return _statement_end(lines, n)
        if text and not text.startswith(("#", "//")):
            return None
    return None


def _statement_end(lines: List[str], start: int) -> Optional[int]:
    """The line a statement starting at line `start` ends on: its brackets balance (outside string literals), the line does not
    end with a `\\` continuation, and the next non-blank line does not carry on the chain with `.`."""
    depth, quote, n = 0, "", start
    while n <= len(lines):
        line, i = lines[n - 1], 0
        while i < len(line):
            ch = line[i]
            if quote:
                if ch == "\\":
                    i += 1
                elif ch == quote:
                    quote = ""
            elif ch in "'\"`":
                quote = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            i += 1
        if quote in ("'", '"'):
            quote = ""                                   # an unclosed one-line string: do not let it swallow the file
        nxt = next((m for m in range(n + 1, len(lines) + 1) if lines[m - 1].strip()), None)
        if depth <= 0 and not quote and not line.rstrip().endswith("\\") and \
                not (nxt and lines[nxt - 1].strip().startswith(".")):
            return n
        n = nxt if depth <= 0 and nxt and not line.rstrip().endswith("\\") else n + 1
    return None


def _unquote(s: str) -> str:
    return re.sub(r"\s+", "", s.replace('"', "'"))
