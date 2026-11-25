"""
===============================================================================
Linear-time regex for locator patterns taken from client source
===============================================================================
A locator such as getByText(/^Save$/i) carries a regular expression written by the client's test author. It is client
data, and Python's `re` backtracks: `(a+)+$` or `.*.*.*X` on a long page text holds a worker for minutes. This module
is the only place such a pattern is run, and it uses RE2 (google-re2), which has no backtracking and runs in time linear
in the text. Anything else is "cannot evaluate" (CannotEvaluate): the caller then makes no uniqueness claim at all,
which sends the heal to a refusal or to a human instead of guessing. It is never answered with Python `re`.

Cannot evaluate: a pattern RE2 cannot express (lookahead, backreference) or longer than MAX_PATTERN_CHARS; a flag that
is not mapped; counted repeats ({n,m}) whose bounds, alone or multiplied, pass MAX_REPEAT (RE2 leaves its DFA for a far
slower NFA on `(.{0,900}|b)*X`); a text longer than MAX_TEXT_CHARS (the text is never cut: a cut changes what `$` matches);
a text RE2 cannot encode (a lone surrogate); a caller whose work budget is spent.

A QUOTED string is never a regex, whatever it looks like. Only a bare `/body/flags` token in TS source, or a Python
`re.compile("body", flags)` argument, is one (see inventory._lit_and_opts).

Features:
    ✓ RegexLit: a str marker type; only values of this type are ever run as a pattern
    ✓ search(): True / False, or raises CannotEvaluate; JS flags i, m, s map to RE2's, g y u d change nothing
    ✓ work_budget(): one count / matched / aria_count call may feed RE2 at most WORK_BUDGET_CHARS characters in all
    ✓ Compiled patterns are cached

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import contextlib
import contextvars
import re
from functools import lru_cache
from typing import Any, Iterator, List, Optional, Union

try:                                    # pragma: no cover - exercised through search()
    import re2 as _re2
except ImportError:                     # without RE2 no regex locator is ever evaluated
    _re2 = None

MAX_PATTERN_CHARS = 1000
MAX_REPEAT = 100                        # largest {n} / {n,m} bound evaluated, and the most their product may reach
MAX_TEXT_CHARS = 4096                   # longest text one pattern is run on
WORK_BUDGET_CHARS = 1_000_000           # characters of text one count() / aria_count() may search in all
_REPEAT_BOUND = re.compile(r"\{(\d{1,9})(?:,(\d{0,9}))?\}")
_FLAG_PREFIX = frozenset("ims")
_NEUTRAL_FLAGS = frozenset("gyud")      # JS flags that do not change whether a text matches


class CannotEvaluate(Exception):
    """A regex locator that this service will not (or cannot) run; the caller reports "unknown", never "no match"."""


class RegexLit(str):
    """The `/body/flags` source of a regex literal that really is one (as opposed to a quoted string)."""


def split_literal(lit: str):
    """(body, flags) of a RegexLit's `/body/flags` text."""
    body, _, flags = lit[1:].rpartition("/")
    return body, flags


_budget: contextvars.ContextVar[Optional[List[int]]] = contextvars.ContextVar("regex_budget", default=None)


@contextlib.contextmanager
def work_budget() -> Iterator[None]:
    """Everything searched inside this block shares one budget of WORK_BUDGET_CHARS; an enclosing block's wins."""
    if _budget.get() is not None:
        yield
        return
    token = _budget.set([WORK_BUDGET_CHARS])
    try:
        yield
    finally:
        _budget.reset(token)


def _repeat_too_large(body: str) -> bool:
    """True when the {n} / {n,m} bounds multiply past MAX_REPEAT (each bound alone may be small)."""
    product = 1
    for m in _REPEAT_BOUND.finditer(body):
        product *= max(int(g) for g in m.groups() if g)
        if product > MAX_REPEAT:
            return True
    return False


@lru_cache(maxsize=256)
def _compile(body: str, flags: str) -> Any:
    if _re2 is None:
        raise CannotEvaluate("regex locators are not evaluated here")
    if len(body) > MAX_PATTERN_CHARS or any(f not in _FLAG_PREFIX and f not in _NEUTRAL_FLAGS for f in flags):
        raise CannotEvaluate("pattern too long or flag not supported")
    if _repeat_too_large(body):
        raise CannotEvaluate("counted repetition too large")
    prefix = "".join(sorted(set(flags) & _FLAG_PREFIX))
    try:
        opts = _re2.Options()
        opts.log_errors = False
        return _re2.compile((f"(?{prefix})" if prefix else "") + body, opts)
    except Exception as exc:            # RE2 rejects what it cannot run in linear time
        raise CannotEvaluate(str(exc)) from exc


def search(body: str, flags: Union[str, bool], text: str) -> bool:
    """Whether the pattern matches somewhere in `text`; CannotEvaluate when it cannot be run. `flags` is a JS flag
    string, or a bool meaning ignore-case."""
    flag_text = ("i" if flags else "") if isinstance(flags, bool) else flags
    compiled = _compile(body, flag_text)
    if len(text) > MAX_TEXT_CHARS:
        raise CannotEvaluate("text too long to match")
    budget = _budget.get()
    if budget is not None:
        if budget[0] < len(text):
            raise CannotEvaluate("regex work budget spent")
        budget[0] -= len(text)
    try:
        return compiled.search(text) is not None
    except (UnicodeError, ValueError) as exc:           # a lone surrogate cannot be encoded for RE2
        raise CannotEvaluate("text cannot be matched") from exc
