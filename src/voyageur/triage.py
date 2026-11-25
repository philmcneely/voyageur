"""
===============================================================================
Triage (is this drift, and which call is the broken one?)
===============================================================================
The first and cheapest step of a heal, with no model. It reads the failure the way the frameworks' own heal scripts do
today, now in one place: an error that names no locator is not locator drift (a wrong value, a crash, a timeout on a
network call), so it ends as `not_drift`. An error that names one is looked up in the source files the client sent. No
matching call, or several that nothing in the stack can tell apart, ends as `needs_human`: the service never edits a call
it is not sure is the broken one.

When exactly one call is the broken one, triage reports where it is (file, character span, line), how it is written
(receiver, language by file extension), the action the line performs on it (fill, click, check...), and every other use of
the same locator in the sent files, so one fix to a shared page object covers every file that spells the locator the same.

Features:
    ✓ not_drift: no locator in the error, or the locator resolved and the expected / received values differed, or the
      locator resolved and the action waited on an inert element
    ✓ needs_human with a reason code: source_not_found (zero matches), ambiguous_source (several, no stack line splits them)
    ✓ Matching tolerates quote style (Playwright for Python prints double quotes, source may use single) and falls back
      from the printed expression to the selector literal, so a get_by_* call is found too
    ✓ A stack frame (file and line) picks among several matches; a frame that still leaves two on one line does not
    ✓ Hostile files (deep nesting, pathological text) are skipped: RecursionError, MemoryError and ValueError never escape
    ✓ Source is only ever scanned as text, never imported or run

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .failing import Call, all_inert, canon, is_assertion_mismatch, iter_calls, printed_expr
from .locators import receiver_of, selectors_from

_TS_EXT = (".ts", ".tsx", ".js", ".mjs", ".cjs")
_ACTION = re.compile(r"(?:\s*\.(?:first|last)\b(?!\s*\())*\s*\.(\w+)\(")      # past Python's .first / .last properties
_CAMEL = re.compile(r"(?<=[a-z0-9])([A-Z])")


@dataclass(frozen=True)
class Use:
    """Another call, in a sent file, that spells the broken locator the same way."""
    path: str
    start: int
    end: int
    line: int
    text: str


@dataclass
class Triage:
    """The outcome: status "located" (the fields below are set), "not_drift" or "needs_human" (reason and why say why)."""
    status: str
    reason: str = ""
    why: str = ""
    path: str = ""
    digest: str = ""
    start: int = 0
    end: int = 0
    line: int = 0
    call: str = ""
    receiver: str = ""
    language: str = ""
    action: Optional[str] = None
    uses: List[Use] = field(default_factory=list)


def _q(s: str) -> str:
    """A call as compared: each quoted string read as its value, whitespace collapsed (failing.canon)."""
    return canon(s)


def _calls(content: str) -> Optional[List[Call]]:
    try:
        return iter_calls(content)
    except (RecursionError, MemoryError, ValueError):
        return None                                      # a hostile file is skipped, never fatal


def _language(path: str, default: str) -> str:
    return "typescript" if path.endswith(_TS_EXT) else "python" if path.endswith(".py") else default


def _action(content: str, end: int) -> Optional[str]:
    m = _ACTION.match(content, end)
    return _CAMEL.sub(r"_\1", m.group(1)).lower() if m else None


def _on_frame(path: str, frame_file: str) -> bool:
    return path == frame_file or path.endswith("/" + frame_file) or frame_file.endswith("/" + path)


def triage(error: str, files: Sequence, stack: Sequence[Tuple[str, int]], client_language: str) -> Triage:
    """Classify a failure against the sent `files` (objects with path, content, digest) and its `stack` of (file, line)."""
    printed, selectors = printed_expr(error), selectors_from(error)
    if not printed and not selectors:
        return Triage("not_drift", "no_locator_in_error", "the error names no locator, so it is not locator drift")
    if is_assertion_mismatch(error):
        return Triage("not_drift", "assertion_mismatch",
                      "the locator found its element and the expected and received values differed")
    if all_inert(error):
        return Triage("not_drift", "element_inert",
                      "the locator found its element, but the element is inert (an open dialog or overlay holds it)")
    found = [(f, c) for f in files for c in (_calls(f.content) or [])]
    levels = []
    if printed:
        levels += [[(f, c) for f, c in found if _q(c.printed) == _q(printed)],
                   [(f, c) for f, c in found if _q(printed) in _q(c.printed)]]
    if selectors:
        levels.append([(f, c) for f, c in found if c.selector in selectors])
    hits = next((h for h in levels if h), [])
    if not hits:
        return Triage("needs_human", "source_not_found", "no call in the sent files matches the locator in the error")
    chosen = hits[0] if len(hits) == 1 else None
    for frame_file, frame_line in stack:
        if chosen is not None:
            break
        on = [(f, c) for f, c in hits if _on_frame(f.path, frame_file) and c.line == frame_line]
        if len(on) == 1:
            chosen = on[0]
        elif len(on) > 1:
            break
    if chosen is None:
        return Triage("needs_human", "ambiguous_source",
                      f"{len(hits)} calls match and no stack line tells them apart")
    f, c = chosen
    same = _q(c.printed)
    uses = [Use(g.path, d.start, d.end, d.line, d.text) for g in files for d in (_calls(g.content) or [])
            if _q(d.printed) == same and not (g is f and d.start == c.start)]
    return Triage("located", path=f.path, digest=f.digest, start=c.start, end=c.end, line=c.line, call=c.text,
                  receiver=receiver_of(c.text), language=_language(f.path, client_language),
                  action=_action(f.content, c.end), uses=uses)
