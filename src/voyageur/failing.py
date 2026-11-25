"""
===============================================================================
Failing-Locator Identification (heal the one the error names)
===============================================================================
A test method usually holds several locators; healing the wrong one just burns
attempts. This module reads the failure the way a person would and returns the
exact locator expression to heal, with its position in the source.

Signals, strongest first:
    1. The locator Playwright PRINTS: `waiting for <locator>` or
       `strict mode violation: <locator> resolved to N elements`. It is matched to a
       locator call in the source by text, so regex-literal, template and variable
       arguments (`getByLabel(/password/i)`) are found, not just quoted strings.
    2. The source line of the failure: `at file:L:C` frames, the `> L |` code-frame
       marker, or a pytest `file.py:L:` frame. The call on that line wins; with a
       column, the call that ends right before it.
    3. Legacy: the first quoted selector literal in the output that exists in the source.
Signal 2 is used only when the output shows a locator failure (waiting for / strict
mode / timeout), so a plain assertion failure is never mistaken for locator drift.

A target is the whole chain, e.g. `page.getByRole('button').filter({...})`, so the
replacement swaps the full expression. Strict-mode violations are reported with N
so a heal can be told to write a MORE SPECIFIC locator.

A run with several failing tests prints several failure blocks. `split_failures` separates
them and `locate_all` returns EVERY distinct failing locator (one per block, deduped by
expression), so one heal round can repair all of them. A block whose locator RESOLVED but whose
assertion failed (`Received`/`unexpected value`) is not locator drift and is skipped.

Features:
    ✓ iter_calls: balanced scanner (strings, template literals, regex literals, chains)
    ✓ printed_expr / frame_lines / locate_failing / failure_identity / excerpt
    ✓ Heals exactly one occurrence (by span), never every textual match

Author: PMAC
===============================================================================
"""
import json
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .bounds import MAX_SOURCE_LINE_CHARS
from .locators import (LOCATOR_CALL, LOCATOR_SPLIT_RE, QUOTED_LITERAL, find_call, first_group, selectors_from)

_LOCATOR_HEAD = r"(?:locator|frameLocator|frame_locator|getBy\w+|get_by_\w+)"
_START_RE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\." + _LOCATOR_HEAD + r"\(")
_CHAIN_RE = re.compile(r"\.(?:first|last|nth|filter|locator|frameLocator|frame_locator|and|or|and_|or_|"
                       r"getBy\w+|get_by_\w+)\(")
_PRINT_START_RE = re.compile(r"(?:strict mode violation:|waiting for)\s+"
                             r"(?=(?:locator|frameLocator|getBy\w+|get_by_\w+)\()")
_PRINT_HEAD_RE = re.compile(r"(?:locator|frameLocator|frame_locator|getBy\w+|get_by_\w+)\(")
_STRICT_RE = re.compile(r"strict mode violation|resolved to (\d+) elements")
_N_RE = re.compile(r"resolved to (\d+) elements")
_SIGNAL_RE = re.compile(r"waiting for|strict mode violation|resolved to \d+ element|"
                        r"[Tt]imeout (?:of )?\d+ms exceeded|locator resolved")
_AT_RE = re.compile(r"^[ \t]*at (?:[^\n]*\()?([^\s()]+?\.(?:ts|tsx|js|mjs|cjs|py)):(\d+):(\d+)\)?[ \t]*\r?$", re.M)
_CODE_FRAME_RE = re.compile(r"^[ \t]*>[ \t]*(\d+)[ \t]*\|", re.M)
_PY_FRAME_RE = re.compile(r"^(\S+\.py):(\d+):", re.M)


@dataclass
class Call:
    """A locator expression found in source: its span, text and 1-based line."""
    start: int
    end: int
    text: str
    line: int

    @property
    def printed(self) -> str:
        """The call as Playwright prints it: no receiver, whitespace-normalised."""
        receiver = LOCATOR_SPLIT_RE.split(self.text, 1)[0]
        return norm(self.text[len(receiver) + 1:])

    @property
    def selector(self) -> str:
        """The first selector literal of the call (or its printed argument text)."""
        m = re.search(LOCATOR_CALL + r"\(\s*" + QUOTED_LITERAL, self.text)
        if m:
            return first_group(m)
        head = _PRINT_HEAD_RE.search(self.text)
        inner = self.text[head.end():] if head else self.text
        return norm(inner.split(")")[0])


@dataclass
class Target:
    """The locator expression chosen to heal, with how it was found and the strict-mode count if any."""
    selector: str
    call: str
    start: int
    end: int
    line: int
    how: str
    printed: str = ""
    strict_n: Optional[int] = None
    block: str = ""


def norm(s: str) -> str:
    """`s` with whitespace runs collapsed and the ends stripped."""
    return re.sub(r"\s+", " ", s).strip()


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "0": "\0"}


def canon(s: str) -> str:
    """A call as compared across languages and printers: every quoted string ('..', "..", `..`) decoded to its value
    and written back one way (JSON, unescaped non-ASCII), whitespace outside strings collapsed. Playwright prints
    `locator("a[b=\\"c\\"]")` for source `locator('a[b="c"]')`: both read the same here. One linear pass."""
    out: List[str] = []
    i, n = 0, len(s)
    tail = ""                                           # the last few characters written: is this locator()'s argument?
    while i < n:
        c = s[i]
        if c in "'\"`":
            j, val = i + 1, []
            while j < n and s[j] != c:
                if s[j] == "\\" and j + 1 < n:
                    e = s[j + 1]
                    if e == "u" and j + 5 < n and all(h in "0123456789abcdefABCDEF" for h in s[j + 2:j + 6]):
                        cp = int(s[j + 2:j + 6], 16)
                        j += 6
                        if 0xD800 <= cp <= 0xDBFF and s[j:j + 2] == "\\u" and all(
                                h in "0123456789abcdefABCDEF" for h in s[j + 2:j + 6]) and 0xDC00 <= int(s[j + 2:j + 6], 16) <= 0xDFFF:
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (int(s[j + 2:j + 6], 16) - 0xDC00)    # a pair: one character
                            j += 6
                        val.append("\ufffd" if 0xD800 <= cp <= 0xDFFF else chr(cp))       # a lone half cannot be encoded
                        continue
                    val.append(_ESCAPES.get(e, e))
                    j += 2
                    continue
                val.append(s[j])
                j += 1
            if j >= n:                                  # unterminated: the rest is compared as written
                out.append(norm(s[i:]))
                break
            value = "".join(val)
            if tail.endswith("locator(") and s[j + 1:j + 2] == ")":
                out.append(")".join(f".locator({json.dumps(p, ensure_ascii=False)}" for p in _selector_parts(value))[9:])
            else:
                out.append(json.dumps(value, ensure_ascii=False))
            tail = '"'
            i = j + 1
            continue
        if c.isspace():
            while i < n and s[i].isspace():
                i += 1
            out.append(" ")
            tail = (tail + " ")[-8:]
            continue
        out.append(c)
        tail = (tail + c)[-8:]
        i += 1
    return "".join(out).strip()


def _selector_parts(value: str) -> List[str]:
    """A locator() selector as Playwright prints it: split at each top-level ` >> ` (outside quotes and brackets), each
    part trimmed and without the `xpath=` prefix Playwright drops for a path starting `/` or `(`."""
    parts, depth, quote, start = [], 0, "", 0
    k, n = 0, len(value)
    while k < n:
        c = value[k]
        if quote:
            if c == "\\":
                k += 2
                continue
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
        elif c in "[(":
            depth += 1
        elif c in "])":
            depth = max(0, depth - 1)
        elif depth == 0 and value.startswith(" >> ", k):
            parts.append(value[start:k])
            start = k + 4
            k += 4
            continue
        k += 1
    parts.append(value[start:])
    out = []
    for p in parts:
        p = p.strip()
        if p.startswith("xpath=") and p[6:7] in ("/", "("):
            p = p[6:]
        out.append(p)
    return out


CALL_WINDOW = 4096            # a locator call longer than this is not read: it is no real locator
SCAN_BUDGET_FACTOR = 2        # iter_calls reads at most this many times the source's length in characters in total
SCAN_BUDGET_FLOOR = 64 * 1024
CHAIN_BUDGET = 4 * CALL_WINDOW   # characters one chain (outside iter_calls) may read in all


def _balanced(text: str, open_idx: int, limit: Optional[int] = None) -> Tuple[int, int]:
    """(index just past the ')' matching the '(' at open_idx or -1, index where the scan stopped). `limit` caps how many
    characters past open_idx are read. The previous significant character is tracked as the scan goes, so a '/' is
    judged in O(1) (rescanning the text before it made an unclosed call quadratic)."""
    depth, i, n = 0, open_idx, len(text)
    if limit is not None:
        n = min(n, open_idx + limit)
    prev = ""                                   # last non-space character outside strings and regex literals
    while i < n:
        c = text[i]
        if c in "'\"`":
            q, i = c, i + 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            prev = q
        elif c == "/" and prev in ("(", ",", ":") and i + 1 < n and text[i + 1] not in "/*":
            i += 1
            in_cls = False
            while i < n and (in_cls or text[i] != "/"):
                if text[i] == "\\":
                    i += 1
                elif text[i] == "[":
                    in_cls = True
                elif text[i] == "]":
                    in_cls = False
                i += 1
            prev = "/"
        elif c in "([{":
            depth += 1
            prev = c
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1, i + 1
            prev = c
        elif not c.isspace():
            prev = c
        i += 1
    return -1, min(i, n)


def balanced_end(text: str, open_idx: int, limit: Optional[int] = None) -> int:
    """Index just past the ')' matching the '(' at open_idx; -1 if unbalanced (or longer than `limit` characters).
    Skips quoted/template strings and regex literals."""
    return _balanced(text, open_idx, limit)[0]


_BARE_NTH_RE = re.compile(r"\.(?:first|last)(?![\w(])")        # Python's `.first` / `.last` are properties


def _extend_chain(text: str, end: int, budget: Optional[List[int]] = None) -> int:
    """`end` moved past the narrowing calls (`.first`, `.filter(...)`, `.locator(...)`) that follow. Every character a
    step reads is taken from `budget` (a one-item list shared with the caller; a private CHAIN_BUDGET when none), and the
    chain stops, short of the unread calls, when it is spent: a chain of thousands of `.filter({ has: ...` calls costs
    a bounded amount."""
    if budget is None:
        budget = [CHAIN_BUDGET]
    while budget[0] > 0:
        bare = _BARE_NTH_RE.match(text, end)
        if bare and _CHAIN_RE.match(text, bare.end()):          # `.first.locator(..)`: still the same expression
            end = bare.end()
        m = _CHAIN_RE.match(text, end)
        if not m:
            return end
        nxt, stopped = _balanced(text, m.end() - 1, CALL_WINDOW)
        budget[0] -= max(stopped - end, 1)
        if nxt < 0:
            return end
        end = nxt
    return end


def iter_calls(source: str) -> List[Call]:
    """Every locator expression in source (receiver through the end of its narrowing chain). Linear in the source: a call
    is read within CALL_WINDOW characters, and the whole scan (calls and chains) has a character budget, so a file made
    of unclosed calls (`page.locator('a` repeated) or of endless chains costs a bounded amount and returns the calls
    found so far. A call on a line longer than MAX_SOURCE_LINE_CHARS is not read: it is minified code, not a test."""
    calls: List[Call] = []
    pos, line, line_at = 0, 1, 0
    budget = [SCAN_BUDGET_FACTOR * len(source) + SCAN_BUDGET_FLOOR]
    line_end = -1                                                # end of the line last measured, to measure it once
    while budget[0] > 0:
        m = _START_RE.search(source, pos)
        if not m:
            break
        if m.start() >= line_end:
            line_end = source.find("\n", m.start())
            line_end = len(source) if line_end < 0 else line_end
            line_start = source.rfind("\n", 0, m.start()) + 1
            long_line = line_end - line_start > MAX_SOURCE_LINE_CHARS
        if long_line:
            pos = line_end
            continue
        end, stopped = _balanced(source, m.end() - 1, CALL_WINDOW)
        budget[0] -= max(stopped - m.start(), 1)
        if end < 0:
            pos = m.end()
            continue
        end = _extend_chain(source, end, budget)
        line += source.count("\n", line_at, m.start())             # incremental: never recount from the start
        line_at = m.start()
        calls.append(Call(m.start(), end, source[m.start():end], line))
        pos = end
    return calls


def printed_of(call_text: str) -> str:
    """A call as Playwright prints it: receiver dropped, whitespace normalised."""
    receiver = LOCATOR_SPLIT_RE.split(call_text, 1)[0]
    return norm(call_text[len(receiver) + 1:])


def printed_expr(error: str) -> Optional[str]:
    """The locator expression Playwright names as failing (whitespace-normalised)."""
    m = _PRINT_START_RE.search(error)
    if not m:
        return None
    head = _PRINT_HEAD_RE.match(error, m.end())
    if head is None:
        return None
    end = balanced_end(error, head.end() - 1)
    if end < 0:
        return None
    return norm(error[m.end():_extend_chain(error, end)])


def strict_count(error: str) -> Optional[int]:
    """How many elements a strict-mode violation resolved to (0 if unstated), None when the error is not one."""
    if not _STRICT_RE.search(error):
        return None
    n = _N_RE.search(error)
    return int(n.group(1)) if n else 0


def _frame_positions(error: str, filename: Optional[str]) -> List[Tuple[int, Optional[int]]]:
    """Ordered (line, col|None) positions in the test file named by the failure."""
    base = filename.rsplit("/", 1)[-1] if filename else None
    pos: List[Tuple[int, Optional[int]]] = []
    for m in _AT_RE.finditer(error):
        if base is None or m.group(1).rsplit("/", 1)[-1] == base:
            pos.append((int(m.group(2)), int(m.group(3))))
    for m in _CODE_FRAME_RE.finditer(error):
        pos.append((int(m.group(1)), None))
    for m in _PY_FRAME_RE.finditer(error):
        if base is None or m.group(1).rsplit("/", 1)[-1] == base:
            pos.append((int(m.group(2)), None))
    return pos


def frame_lines(error: str, filename: Optional[str] = None) -> List[int]:
    """The line numbers of the failure frames that are in the test file, in order."""
    return [ln for ln, _ in _frame_positions(error, filename)]


def _target(c: Call, how: str, printed: str, strict_n: Optional[int]) -> Target:
    return Target(c.selector, c.text, c.start, c.end, c.line, how, printed, strict_n)


def locate_failing(error: str, source: str, filename: Optional[str] = None) -> Optional[Target]:
    """The single locator expression in `source` that this failure is about, or None."""
    calls = iter_calls(source)
    printed = printed_expr(error) or ""
    strict_n = strict_count(error)
    positions = _frame_positions(error, filename)
    lines = [ln for ln, _ in positions]

    if printed:
        want = canon(printed)
        hits = [c for c in calls if canon(c.printed) == want] or [c for c in calls if want in canon(c.printed)]
        if hits:
            on_frame = [c for c in hits if c.line in lines]
            return _target((on_frame or hits)[0], "printed", printed, strict_n)

    if _SIGNAL_RE.search(error):
        for line, col in positions:
            on_line = [c for c in calls if c.line == line]
            if not on_line:
                continue
            if col is not None and len(on_line) > 1:
                line_start = source.rfind("\n", 0, on_line[0].start) + 1
                before = [c for c in on_line if c.end - line_start <= col]
                if before:
                    return _target(before[-1], "frame", printed, strict_n)
            return _target(on_line[0], "frame", printed, strict_n)

    for sel in selectors_from(error):
        call = find_call(source, sel)
        if call:
            c = next((c for c in calls if c.text.startswith(call)), None)
            if c:
                return _target(c, "legacy", printed, strict_n)
    return None


_BLOCK_RE = re.compile(r"^[ \t]*\d+\) \S+\.(?:ts|tsx|js|mjs|cjs):\d+:\d+", re.M)   # not the "1) <input ...>" match lists
_ASSERT_RE = re.compile(r"Received (?:string|value)|unexpected value|Expected (?:substring|string|pattern|value)\b")
_WAIT_STATES = ("visible", "enabled", "editable")


def _not_found(block: str) -> bool:
    """`element(s) not found`, or a `waiting for ...` line that names a visibility/enabled/editable state. Plain
    finds, one per line: the regex `waiting for .*(...)` backtracked over the rest of the line once per repeat of
    "waiting for", so a 64 KB error built from repeats took seconds."""
    if "element(s) not found" in block:
        return True
    for line in block.splitlines():
        i = line.find("waiting for ")
        if i >= 0 and any(s in line[i + 12:] for s in _WAIT_STATES):
            return True
    return False


def split_failures(error: str) -> List[str]:
    """One text block per failing test (Playwright's numbered `  N) file › title` sections).
    Output without numbered sections (pytest, a single failure) is one block."""
    starts = [m.start() for m in _BLOCK_RE.finditer(error)]
    if not starts:
        return [error]
    return [error[a:b] for a, b in zip(starts, starts[1:] + [len(error)], strict=True)]


def is_assertion_mismatch(block: str) -> bool:
    """The locator resolved and the expected/received VALUES differed: the assertion is wrong or the
    app is, not the locator. Healing the locator would only flip it around."""
    return bool("locator resolved to <" in block and _ASSERT_RE.search(block) and not _not_found(block))


# `  11 |   await ...` / `> 12 | ...`: a quoted source line; `     |      ^`. One whitespace run per position, so a long blank
# line is read once (two adjacent `\s*` split it every way before failing)
_CODE_FRAME = re.compile(r"^\s*(?:>\s*)?\d+\s\|")
_CARET = re.compile(r"^\s*\|\s*\^")


def without_code_frame(block: str) -> str:
    """The error without the source lines Playwright (TS) quotes around the failure: those name the neighbouring statements'
    locators, the healed one included, not the one that failed."""
    return "\n".join(ln for ln in block.splitlines() if not (_CODE_FRAME.match(ln) or _CARET.match(ln)))


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_INERT_LINE = re.compile(r"[ \t]*-[ \t]*element is inert[ \t]*")
_NOT_HELD = ("element(s) not found", "element was detached")


def is_inert(block: str) -> bool:
    """The locator found its element and the action waited for it to stop being inert (Playwright 1.65+): an open dialog or
    an overlay holds it, so the locator is right. A heal would move the call to some element that is not inert.

    The inert note must be a call-log line of its own after the `locator resolved to` line, so page text inside the resolved
    element cannot fake it. An element that was detached (a re-render) or not found is not held, and quoted source lines
    are not read. The actionability wait ("waiting for element to be visible, enabled and stable") is on the found element."""
    lines = [_ANSI.sub("", ln) for ln in without_code_frame(block).splitlines()]
    if any(s in ln for ln in lines for s in _NOT_HELD):
        return False
    resolved = False
    for ln in lines:
        if "locator resolved to <" in ln:
            resolved = True
        elif resolved and _INERT_LINE.fullmatch(ln):
            return True
    return False


def all_inert(error: str) -> bool:
    """Every failure block in the output is an inert wait: one inert block must not hide drift in another."""
    return all(is_inert(b) for b in split_failures(error))


def is_missing_locator(block: str) -> bool:
    """A locator found nothing: it is named, the wait for it timed out or found no element, and nothing resolved. A value that
    differed, a strict-mode match of several elements or an error that names no locator is something else. Quoted source
    lines are not read."""
    block = without_code_frame(block)
    if not (printed_expr(block) or selectors_from(block)) or "locator resolved to" in block or "strict mode violation" in block:
        return False
    return _not_found(block) or ("Timeout" in block and "waiting for " in block)


def locate_all(error: str, source: str, filename: Optional[str] = None) -> Tuple[List[Target], int]:
    """Every distinct failing locator in the run output, in order, plus how many failure blocks were
    assertion mismatches (skipped). Deduped by call text: one repair per expression."""
    targets: List[Target] = []
    seen = set()
    mismatches = 0
    for block in split_failures(error):
        if is_assertion_mismatch(block):
            mismatches += 1
            continue
        t = locate_failing(block, source, filename)
        if t is None or t.call in seen:
            continue
        seen.add(t.call)
        t.block = block
        targets.append(t)
    return targets, mismatches


def failing_set(error: str, filename: Optional[str] = None) -> frozenset:
    """Identity of everything that is failing: {(printed expr or selector, first frame line)} per block."""
    return frozenset(failure_identity(b, filename) for b in split_failures(error) if not is_assertion_mismatch(b))


def has_locator_signal(error: str) -> bool:
    """True when the output shows a locator failure (a printed locator, a selector or a timeout signal)."""
    return bool(printed_expr(error) or selectors_from(error) or _SIGNAL_RE.search(error))


def failure_identity(error: str, filename: Optional[str] = None) -> Tuple[str, Optional[int]]:
    """What the failure is about: (printed expression or first selector, first frame line).
    Used to tell whether a patch moved the failure to something else."""
    expr = printed_expr(error) or next(iter(selectors_from(error)), "")
    lines = frame_lines(error, filename)
    return expr, (lines[0] if lines else None)


def excerpt(error: str, limit: int = 1800) -> str:
    """The part of the output that describes the failure (strict-mode lists included), not the tail."""
    found = (error.find("strict mode violation"), error.find("Error:"), error.find("waiting for"))
    marks = [i for i in found if i >= 0]
    start = max(min(marks) - 60, 0) if marks else max(len(error) - limit, 0)
    return error[start:start + limit]
