"""
===============================================================================
Frames (an in-frame step is healed inside its frame, never on the page around it)
===============================================================================
A test line such as page.frame_locator("#payment-frame").locator("input[name=card]") names a frame first and an element in
it second. The inventory lists the elements of a same-origin frame with the frame's name or id, and the page-level elements
without one, so a pick for such a step must come from that frame only: a page-level element is never what the step
meant (it was what the step could not reach), and neither is an element of another frame.

scope_of reads the broken call, finds the frame hop in its text (frame_locator / frameLocator, or a locator followed by
content_frame / contentFrame), resolves the hop's selector to a frame listed in the inventory (the iframe element the selector
names: by id, name, title or a hook; else the frame name written in it) and returns the candidate pool: the in-frame elements of
that frame, with an Inventory over them for writing. prefixed() puts the same hop in front of a locator written for one of them,
in both client languages, so the new locator is the old frame hop plus the new in-frame locator.

When the call ends in the hop itself (the broken thing is the frame locator) the target is the iframe element on the page and
there is no scope. When the hop cannot be resolved to a listed frame the scope has no frame and an empty pool, which ends the
heal as target_missing unless frame_heal.py can heal the hop itself (a renamed frame, or an element that left its frame); then
the Scope it returns carries a HopHeal: the part of the call after the hop, kept, and the new text up to the hop.

Features:
    ✓ frame_hop(): (prefix text, hop selector, writable) for the last frame hop of a call, or None
    ✓ last_hop(): where that hop starts and ends in the call text
    ✓ hop_absent(): the hop's selector is short and readable (id, name, title, test hook) and names no listed iframe, on a complete list
    ✓ scope_of(): Scope(prefix, frame, elements, inventory, writable) for a call, or None for a page-level step
    ✓ HopHeal: what a healed hop keeps (the inner call text) and the new text in front of it, best first
    ✓ prefixed(): a Written locator rewritten with the frame hop in front (code in both languages and the neutral chain)
    ✓ Only frame_locator / frameLocator hops can be written (the chain grammar has no content_frame); a content_frame hop
      still limits the candidates to its frame

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from .failing import balanced_end
from .inventory import Inventory
from .locator_policy import Written, _swap_receiver
from .locator_steps import chain_to_data, try_parse

_HOP = re.compile(r"\.(frame_locator|frameLocator)\(")
_CONTENT = re.compile(r"\.(content_frame|contentFrame)(?:\(\s*\))?")
_LOCATOR_ARG = re.compile(r"""\.locator\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")\s*\)""")
_STRING_ARG = re.compile(r"""\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
_PARTS = re.compile(r"""#([\w-]+)|\[\s*(?:id|name|title|data-[\w-]+)\s*([~|^$*]?)=\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+))\s*\]""")
_POSITION = re.compile(r"""\s*\.(?:(?:first|last)\b(?:\s*\(\s*\))?|nth\(\s*-?\d+\s*\))""")   # .first / .last / .nth(n)


@dataclass
class HopHeal:
    """A healed frame hop. `old` is the call text up to and including the broken hop, `inner` the call text after it, which
    stays as the test has it; `prefixes` are the new text in front of `inner`, best first (`[receiver]` when the frame is
    gone and the element is in the page itself); `note` says what was found, for the record."""
    old: str
    inner: str
    prefixes: List[str]
    note: str


@dataclass
class Scope:
    """The pool for a step inside a frame: `prefix` is the call text up to and including the hop (the healed one when `hop` is
    set), `frame` the frame name the hop resolved to (None when it did not resolve), `elements` the frame's elements with the
    frame key removed. `why` says what is missing when the hop did not resolve and could not be healed."""
    prefix: str
    frame: Optional[str]
    elements: List[Dict[str, Any]] = field(default_factory=list)
    inventory: Optional[Inventory] = None
    writable: bool = True
    hop: Optional[HopHeal] = None
    why: str = ""


def last_hop(call: str) -> Optional[Tuple[int, int, str, bool]]:
    """(start, end, selector, writable) of the last frame hop of a call, or None. call[start:end] is the hop with its
    `.first` / `.last` / `.nth(n)`; start is -1 for a `content_frame` hop (it has no text of its own to replace)."""
    best: Optional[Tuple[int, str, bool, int]] = None
    for m in _HOP.finditer(call):
        end = balanced_end(call, m.end() - 1)
        arg = _STRING_ARG.match(call, m.end() - 1)
        if end > 0 and arg and (best is None or end > best[0]):
            best = (end, arg.group(1) if arg.group(1) is not None else arg.group(2), True, m.start())
    for m in _CONTENT.finditer(call):
        prev = None
        for lm in _LOCATOR_ARG.finditer(call[:m.start()]):
            if lm.end() == m.start():
                prev = lm
        if prev and (best is None or m.end() > best[0]):
            best = (m.end(), prev.group(1) if prev.group(1) is not None else prev.group(2), False, -1)
    if best is None:
        return None
    end = best[0]
    m = _POSITION.match(call, end)
    if m:                                               # the frame the hop picks among several: part of the prefix
        end = m.end()
    return best[3], end, best[1], best[2]


def frame_hop(call: str) -> Optional[Tuple[str, str, bool]]:
    """(text of the call up to and including its last frame hop, the hop's selector, writable) or None when the call has
    no frame hop or ends in it (then the iframe itself is the target)."""
    hop = last_hop(call)
    if hop is None or not call[hop[1]:].strip():
        return None
    return call[:hop[1]], hop[2], hop[3]


def _names_of(selector: str) -> List[Tuple[str, str]]:
    """(operator, value) of each name the selector gives: `#id` and `[attr=v]` are "", `[attr^=v]` is "^", and so on."""
    out = []
    for m in _PARTS.finditer(selector):
        if m.group(1) is not None:
            out.append(("", m.group(1)))
        else:
            out.append((m.group(2), next(g for g in m.groups()[2:] if g is not None)))
    return out


def _meets(have: str, op: str, value: str) -> bool:
    """CSS attribute matching: = equal, ^= starts, $= ends, *= contains, ~= one whole word, |= equal or value-…"""
    if op == "^":
        return bool(value) and have.startswith(value)
    if op == "$":
        return bool(value) and have.endswith(value)
    if op == "*":
        return bool(value) and value in have
    if op == "~":
        return value in have.split()
    if op == "|":
        return have == value or have.startswith(value + "-")
    return have == value


def resolve_frame(selector: str, items: List[Dict[str, Any]]) -> Optional[str]:
    """The frame name (what the probe stores in `frame`) the hop selector names, or None."""
    frames = {str(e["frame"]) for e in items if e.get("frame")}
    wanted = _names_of(selector)
    for e in items:
        if (e.get("tag") or "") != "iframe" or not wanted:
            continue
        have = [str(e.get(k)) for k in ("id", "name_attr", "title", "testid", "data_test", "data_qa", "data_cy") if e.get(k)]
        if any(_meets(h, op, v) for h in have for op, v in wanted):
            return str(e.get("name_attr") or e.get("id") or "") or None
    for op, v in wanted:
        hits = [f for f in frames if _meets(f, op, v)]
        if len(hits) == 1:
            return hits[0]
    return None


MAX_HOP_SELECTOR = 300         # a longer hop selector is not read (and is not matched against the pattern below)
# the three value spellings start with different characters, so a value can be read one way only: no backtracking between them
_READABLE = re.compile(r"""^\s*(?:iframe|frame)?(?:#[\w-]+|\[\s*(?:id|name|title|data-testid|data-test|data-qa|data-cy)\s*[~|^$*]?=\s*"""
                       r"""(?:"[^"]*"|'[^']*'|[^\]\s"'][^\]\s]*)\s*\])+\s*$""")


def hop_absent(selector: str, flat: List[Dict[str, Any]], complete: bool) -> bool:
    """True when the hop's selector says nothing but ids, names, titles and test hooks (what the listing carries of an iframe)
    and no listed iframe has them. A selector this cannot read (a class, a src, a combinator, one over MAX_HOP_SELECTOR
    characters) or a list cut short could still hold the frame, so it is not absent."""
    return (complete and len(selector) <= MAX_HOP_SELECTOR and bool(_READABLE.match(selector))
            and resolve_frame(selector, flat) is None)


def scope_of(call: str, flat: List[Dict[str, Any]], complete: bool, copy_more: int = 0) -> Optional[Scope]:
    """The scope of a step whose call enters a frame, or None for a page-level step. `flat` is every inventory entry;
    `copy_more` the probe's count of copy-holding elements it left out (the frame's own leaves are dropped first, so a frame
    can carry no `copy_cut` item and still have lost some)."""
    hop = frame_hop(call)
    if hop is None:
        return None
    prefix, selector, writable = hop
    name = resolve_frame(selector, flat)
    if name is None:
        return Scope(prefix, None, [], None, writable)
    inside = [{k: v for k, v in e.items() if k != "frame"} for e in flat if e.get("frame") == name]
    cut = 1 if any(e.get("copy_cut") for e in flat) else 0            # the page's copy list was cut: so is this frame's view of it
    inv = Inventory(inside, complete, "", max(cut, copy_more))             # the scope's elements ARE the inventory's (writing compares identity)
    return Scope(prefix, name, inv.elements, inv, writable)


_PY_POSITION = re.compile(r"\.(first|last)\b(?!\s*\()")
_TS_POSITION = re.compile(r"\.(first|last)\s*\(\s*\)")
_QUOTED = re.compile(r"""('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`)""")


def _swap_hop_names(text: str) -> str:
    """The hop in the other language: frame_locator <-> frameLocator, Python's .first / .last <-> TypeScript's .first()."""
    to_ts = "frame_locator(" in text
    out = []
    for i, part in enumerate(_QUOTED.split(text)):        # odd parts are string literals: never rewritten
        if i % 2 == 0:
            part = (_PY_POSITION.sub(r".\1()", part.replace("frame_locator(", "frameLocator(")) if to_ts
                    else _TS_POSITION.sub(r".\1", part.replace("frameLocator(", "frame_locator(")))
        out.append(part)
    return "".join(out)


def prefixed(w: Written, prefix: str, receiver: str, language: str,
             prefix_other: Optional[str] = None) -> Optional[Written]:
    """`w` (written against `receiver`) with the frame hop `prefix` in front of its locator, in both languages and as a
    neutral chain; None when the result does not parse. `prefix_other` is the prefix in the other language when the
    caller wrote it (a row scope); by default it is `prefix` with the hop names swapped."""
    other = "typescript" if language == "python" else "python"
    other_receiver = _swap_receiver(receiver, other)
    if not prefix.startswith(receiver) or (prefix_other is not None and not prefix_other.startswith(other_receiver)):
        return None
    if prefix_other is None:
        prefix_other = other_receiver + _swap_hop_names(prefix[len(receiver):])
    code: Dict[str, str] = {}
    for lang, recv, pre in ((language, receiver, prefix), (other, other_receiver, prefix_other)):
        text = w.code.get(lang, "")
        if not text.startswith(recv):
            return None
        code[lang] = pre + text[len(recv):]
    chain = try_parse(code[language].replace(receiver, "page", 1), language)
    if chain is None or try_parse(code[other].replace(other_receiver, "page", 1), other) is None:
        return None
    return replace(w, code=code, neutral=chain_to_data(chain))
