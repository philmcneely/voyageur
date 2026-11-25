"""
===============================================================================
Locator Chain Parser (a locator is read as data, never run as code)
===============================================================================
A locator that reaches the service as text (a client's failing line, a locator the service itself wrote) has
to be understood without ever being run. This module turns that text into a list of steps (method name,
literal arguments, literal options) with a real parser, and rejects everything it does not recognise. The
steps are plain data: they can be compared, normalised, rendered back as Python or TypeScript, or replayed
by a client through Playwright's own API. Nothing here evaluates the text it is given.

Accepted (and nothing else): a chain that starts at the receiver `page` (or `this.page` / `self.page`, as page
objects write it) and continues with locator calls
only (locator, frameLocator, getBy*, first, last, nth, filter, and, or and their Python spellings).
Every argument is a string, a number, a boolean, null, a regex literal (TS only) or, where Playwright
takes one (has / hasNot / and / or), a nested chain under the same rules. Options are `{name: literal}`
objects (TS) or keyword arguments (Python) whose names are in a fixed list.
Rejected: subscripts (`x['constructor']`), identifiers, calls on anything but a locator, template
literals with `${}`, spread, computed keys, comments, statements, operators, arrays, `await`.

Features:
    ✓ TS / JS: a hand-written tokenizer and recursive-descent parser (no regex allowlist over the raw text)
    ✓ Python: the ast module, walked with an explicit accept list (no compile, no eval)
    ✓ One shared step model, so both languages are checked against the same method / option tables
    ✓ Size limits (expression length, chain length, nesting depth, number range) against hostile input
    ✓ Steps serialise to a language-neutral JSON form (chain_to_data), so one locator can be written for either language

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import ast
import json
import math
import re
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, NoReturn, Optional, Tuple

from .bounds import MAX_EXPR_CHARS      # the cap lives in bounds.py (re-exported here)

MAX_STEPS = 24               # calls in one chain
MAX_DEPTH = 4                # nested chains (filter({ has: page.locator(...) }) is depth 1)
MAX_OBJECT_KEYS = 16
MAX_INT = 10 ** 9            # nth(), level: far beyond any real page, small enough to cross JSON and JS unchanged
MAX_NUMBER_DIGITS = 15
MAX_REASON_CHARS = 200       # a rejection reason may quote a name from the proposal; it is cut to this

ROOT = "page"
OWNERS = ("this", "self")     # `this.page` / `self.page`: the page held by a page object

STR, STR_RE, INT, BOOL, CHAIN = "a string", "a string or regex", "an integer", "a boolean", "a locator chain"
_WHITESPACE = " \t\r\n"
_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]{0,%d})(?:\.[0-9]{1,%d})?" % (MAX_NUMBER_DIGITS - 1, MAX_NUMBER_DIGITS))
_REGEX_FLAGS = frozenset("dgimsuvy")
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v"}
_HEX = frozenset("0123456789abcdefABCDEF")


class ChainRejected(ValueError):
    """The expression is not a plain locator chain; the message says why (never executed or echoed back verbatim)."""


@dataclass(frozen=True)
class Regex:
    """A TS regex literal: replayed as `new RegExp(source, flags)`."""
    source: str
    flags: str


@dataclass(frozen=True)
class Step:
    """One call (or, for Python's .first / .last, one property) of a locator chain."""
    method: str
    args: Tuple[Any, ...] = ()
    options: Dict[str, Any] = field(default_factory=dict)
    prop: bool = False


@dataclass(frozen=True)
class Chain:
    """A parsed locator chain rooted at `page`."""
    steps: Tuple[Step, ...]


@dataclass(frozen=True)
class MethodSpec:
    """What a chain method takes: positional argument kinds, whether it takes options, where it may sit."""
    positional: Tuple[str, ...] = ()
    options: bool = False
    source: bool = False        # may start a chain (it picks elements; first / nth / filter only narrow)
    prop: bool = False          # Python's .first / .last: a property, not a call


def _methods(py: bool) -> Dict[str, MethodSpec]:
    def name(ts: str, snake: str) -> str:
        return snake if py else ts
    table = {
        name("locator", "locator"): MethodSpec((STR,), True, source=True),
        name("frameLocator", "frame_locator"): MethodSpec((STR,), source=True),
        name("getByRole", "get_by_role"): MethodSpec((STR,), True, source=True),
        name("getByText", "get_by_text"): MethodSpec((STR_RE,), True, source=True),
        name("getByLabel", "get_by_label"): MethodSpec((STR_RE,), True, source=True),
        name("getByPlaceholder", "get_by_placeholder"): MethodSpec((STR_RE,), True, source=True),
        name("getByAltText", "get_by_alt_text"): MethodSpec((STR_RE,), True, source=True),
        name("getByTitle", "get_by_title"): MethodSpec((STR_RE,), True, source=True),
        name("getByTestId", "get_by_test_id"): MethodSpec((STR_RE,), source=True),
        "nth": MethodSpec((INT,)),
        "filter": MethodSpec(options=True),
        name("and", "and_"): MethodSpec((CHAIN,)),
        name("or", "or_"): MethodSpec((CHAIN,)),
    }
    if py:
        table["first"] = MethodSpec(prop=True)
        table["last"] = MethodSpec(prop=True)
    else:
        table["first"] = MethodSpec()
        table["last"] = MethodSpec()
    return table


def _options(py: bool) -> Dict[str, str]:
    def name(ts: str, snake: str) -> str:
        return snake if py else ts
    kinds = {name("hasText", "has_text"): STR_RE, name("hasNotText", "has_not_text"): STR_RE, "name": STR_RE,
             "has": CHAIN, name("hasNot", "has_not"): CHAIN, "level": INT}
    for flag in ("exact", "checked", "disabled", "expanded", "pressed", "selected", "visible",
                 name("includeHidden", "include_hidden")):
        kinds[flag] = BOOL
    return kinds


@dataclass(frozen=True)
class Language:
    """The method and option tables one language is checked against."""
    methods: Dict[str, MethodSpec]
    options: Dict[str, str]
    py: bool


TS = Language(_methods(False), _options(False), False)
PY = Language(_methods(True), _options(True), True)

# the names a replaying client accepts: it can re-check every step against these, whatever the parser said
METHOD_NAMES = {"typescript": sorted(TS.methods), "python": sorted(PY.methods)}
OPTION_NAMES = {"typescript": sorted(TS.options), "python": sorted(PY.options)}
PROPERTY_NAMES = sorted(n for n, s in PY.methods.items() if s.prop)


def _reject(why: str) -> NoReturn:
    raise ChainRejected(why[:MAX_REASON_CHARS])


def _kind_ok(value: Any, kind: str) -> bool:
    if kind == STR:
        return isinstance(value, str)
    if kind == STR_RE:
        return isinstance(value, (str, Regex))
    if kind == INT:
        return type(value) is int and abs(value) <= MAX_INT
    if kind == BOOL:
        return type(value) is bool
    return isinstance(value, Chain)


def _make_step(lang: Language, method: str, args: List[Any], options: Dict[str, Any], prop: bool) -> Step:
    spec = lang.methods.get(method)
    if spec is None:
        _reject(f"{method!r} is not a locator method")
    if prop != spec.prop:
        _reject(f".{method} must be used as a property" if spec.prop else f".{method} is not a property")
    if options and not spec.options:
        _reject(f"{method}() takes no options")
    if len(args) != len(spec.positional):
        _reject(f"{method}() takes {len(spec.positional)} positional argument(s), got {len(args)}")
    for value, wanted in zip(args, spec.positional):
        if not _kind_ok(value, wanted):
            _reject(f"{method}(): an argument must be {wanted}")
    for key, value in options.items():
        option_kind = lang.options.get(key)
        if option_kind is None:
            _reject(f"{method}(): {key!r} is not a locator option")
        if not _kind_ok(value, option_kind):
            _reject(f"{method}(): option {key!r} must be {option_kind}")
    return Step(method, tuple(args), dict(options), prop)


def _check_chain(lang: Language, steps: List[Step]) -> Chain:
    if not steps:
        _reject("not a call chain")
    if len(steps) > MAX_STEPS:
        _reject("chain too long")
    if not lang.methods[steps[0].method].source:
        _reject(f"first call {steps[0].method!r} is not a locator")
    return Chain(tuple(steps))


# ---- TypeScript / JavaScript -----------------------------------------------------------------
class _Reader:
    """Cursor over the expression text with the token readers the parser needs."""

    def __init__(self, text: str):
        self.s, self.i = text, 0

    def peek(self) -> str:
        """The next significant character ('' at the end); whitespace is skipped."""
        while self.i < len(self.s) and self.s[self.i] in _WHITESPACE:
            self.i += 1
        return self.s[self.i] if self.i < len(self.s) else ""

    def take(self, ch: str) -> None:
        if self.peek() != ch:
            _reject(f"expected {ch!r} at position {self.i}")
        self.i += 1

    def ident(self) -> str:
        self.peek()
        m = _IDENT_RE.match(self.s, self.i)
        if not m:
            _reject(f"expected a name at position {self.i}")
        self.i = m.end()
        return m.group(0)

    def string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out: List[str] = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == quote:
                self.i += 1
                return "".join(out)
            if c == "\\":
                out.append(self._escape())
                continue
            if quote == "`" and c == "$" and self.s[self.i + 1:self.i + 2] == "{":
                _reject("template literal interpolation (${...}) is not a literal")
            if c in "\r\n" and quote != "`":
                _reject("line break inside a string")
            out.append(c)
            self.i += 1
        _reject("unterminated string")

    def _escape(self) -> str:
        self.i += 1                                     # past the backslash
        c = self.s[self.i:self.i + 1]
        if not c:
            _reject("unterminated string")
        self.i += 1
        if c in _SIMPLE_ESCAPES:
            return _SIMPLE_ESCAPES[c]
        if c == "0" and not self.s[self.i:self.i + 1].isdigit():
            return "\0"
        if c.isdigit():
            _reject("octal escape in a string")
        if c in "\r\n":                                 # line continuation
            if c == "\r" and self.s[self.i:self.i + 1] == "\n":
                self.i += 1
            return ""
        if c == "x":
            return self._hex_escape(2)
        if c == "u":
            if self.s[self.i:self.i + 1] == "{":
                end = self.s.find("}", self.i)
                digits = self.s[self.i + 1:end] if end > 0 else ""
                if not digits or len(digits) > 6 or not set(digits) <= _HEX or int(digits, 16) > 0x10FFFF:
                    _reject("bad unicode escape")
                self.i = end + 1
                return chr(int(digits, 16))
            return self._hex_escape(4)
        return c

    def _hex_escape(self, width: int) -> str:
        digits = self.s[self.i:self.i + width]
        if len(digits) != width or not set(digits) <= _HEX:
            _reject("bad hex escape")
        self.i += width
        return chr(int(digits, 16))

    def number(self) -> Any:
        m = _NUMBER_RE.match(self.s, self.i)
        nxt = self.s[m.end():m.end() + 1] if m else ""
        if not m or nxt.isalnum() or nxt in ("_", "$", "."):
            _reject(f"not a plain number at position {self.i}")
        self.i = m.end()
        value = float(m.group(0)) if "." in m.group(0) else int(m.group(0))
        if abs(value) > MAX_INT:
            _reject("number out of range")
        return value

    def regex(self) -> Regex:
        start = self.i + 1
        if self.s[start:start + 1] in ("/", "*", ""):
            _reject("a comment or an empty regex")
        j, in_class = start, False
        while j < len(self.s) and (in_class or self.s[j] != "/"):
            c = self.s[j]
            if c in "\r\n":
                _reject("line break inside a regex")
            if c == "\\":
                j += 1
            elif c == "[":
                in_class = True
            elif c == "]":
                in_class = False
            j += 1
        if j >= len(self.s):
            _reject("unterminated regex")
        source, j = self.s[start:j], j + 1
        flags_start = j
        while j < len(self.s) and self.s[j].isalpha():
            j += 1
        flags = self.s[flags_start:j]
        if len(set(flags)) != len(flags) or not set(flags) <= _REGEX_FLAGS:
            _reject("bad regex flags")
        if self.s[j:j + 1].isalnum() or self.s[j:j + 1] in ("_", "$"):
            _reject("bad regex flags")
        self.i = j
        return Regex(source, flags)


def _ts_value(r: _Reader, depth: int) -> Any:
    c = r.peek()
    if c in ("'", '"', "`"):
        return r.string()
    if c == "/":
        return r.regex()
    if c == "{":
        return _ts_object(r, depth)
    if c == "-" or c.isdigit():
        return r.number()
    word = r.ident()
    if word in ("true", "false", "null"):
        return {"true": True, "false": False, "null": None}[word]
    if word == ROOT or word in OWNERS:
        r.i -= len(word)
        return _ts_chain(r, depth + 1)
    return _reject(f"identifier {word!r} is not a literal")


def _ts_object(r: _Reader, depth: int) -> Dict[str, Any]:
    r.take("{")
    out: Dict[str, Any] = {}
    while r.peek() != "}":
        if len(out) >= MAX_OBJECT_KEYS:
            _reject("too many options")
        c = r.peek()
        if c in ("'", '"'):
            key = r.string()
        elif _IDENT_RE.match(r.s, r.i):
            key = r.ident()
        else:
            return _reject("an option name must be a plain name or a quoted string (no spread, no computed keys)")
        if key in out:
            _reject(f"option {key!r} given twice")
        r.take(":")
        out[key] = _ts_value(r, depth)
        if r.peek() == ",":
            r.i += 1
        elif r.peek() != "}":
            _reject(f"expected ',' or '}}' at position {r.i}")
    r.take("}")
    return out


def _ts_step(r: _Reader, depth: int) -> Step:
    method = r.ident()
    r.take("(")
    args: List[Any] = []
    while r.peek() != ")":
        args.append(_ts_value(r, depth))
        if r.peek() == ",":
            r.i += 1
        elif r.peek() != ")":
            _reject(f"expected ',' or ')' at position {r.i}")
    r.take(")")
    options: Dict[str, Any] = {}
    if args and isinstance(args[-1], dict):
        options = args.pop()
    if any(isinstance(a, dict) for a in args):
        _reject("an option object must be the last argument")
    return _make_step(TS, method, args, options, False)


def _ts_chain(r: _Reader, depth: int) -> Chain:
    if depth > MAX_DEPTH:
        _reject("locator chains nested too deeply")
    root = r.ident()
    if root in OWNERS:
        r.take(".")
        root = r.ident()
    if root != ROOT:
        _reject(f"a locator chain must start at {ROOT!r}")
    steps: List[Step] = []
    while r.peek() == ".":
        r.i += 1
        steps.append(_ts_step(r, depth))
        if len(steps) > MAX_STEPS:
            _reject("chain too long")
    return _check_chain(TS, steps)


def parse_ts_chain(expr: str) -> Chain:
    """Parse a TS / JS locator chain (`page.getByRole('button', { name: 'Go' }).first()`); ChainRejected otherwise."""
    if not isinstance(expr, str) or len(expr) > MAX_EXPR_CHARS:
        _reject("not a short string")
    r = _Reader(expr)
    chain = _ts_chain(r, 0)
    if r.peek() != "":
        _reject(f"unexpected {r.peek()!r} after the locator chain")
    return chain


# ---- Python ----------------------------------------------------------------------------------
def _py_value(node: ast.AST, depth: int) -> Any:
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, float) and not math.isfinite(v):
            _reject("number out of range")
        if v is None or isinstance(v, (str, bool, int, float)):
            return v
        _reject("not a plain literal")
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant) and type(node.operand.value) in (int, float)):
        number: Any = node.operand.value
        return -number if isinstance(node.op, ast.USub) else number
    if isinstance(node, (ast.Call, ast.Attribute, ast.Name)):
        return _py_chain(node, depth + 1)
    return _reject(f"{type(node).__name__} is not a literal")


def _is_page(node: ast.AST) -> bool:
    """`page`, `this.page` or `self.page`."""
    if isinstance(node, ast.Name):
        return node.id == ROOT
    return (isinstance(node, ast.Attribute) and node.attr == ROOT and isinstance(node.value, ast.Name)
            and node.value.id in OWNERS)


def _py_chain(node: ast.AST, depth: int) -> Chain:
    if depth > MAX_DEPTH:
        _reject("locator chains nested too deeply")
    steps: List[Step] = []
    while True:
        if isinstance(node, ast.Call):
            func = node.func
            if not isinstance(func, ast.Attribute):
                _reject("a call on something that is not a locator")
            if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
                _reject("no * or ** unpacking")
            args = [_py_value(a, depth) for a in node.args]
            options = {str(k.arg): _py_value(k.value, depth) for k in node.keywords}
            steps.append(_make_step(PY, func.attr, args, options, False))
            node = func.value
        elif _is_page(node):
            break
        elif isinstance(node, ast.Attribute):
            steps.append(_make_step(PY, node.attr, [], {}, True))
            node = node.value
        else:
            _reject(f"{type(node).__name__} is not part of a locator chain")
        if len(steps) > MAX_STEPS:
            _reject("chain too long")
    steps.reverse()
    return _check_chain(PY, steps)


def parse_py_chain(expr: str) -> Chain:
    """Parse a Python locator chain (`page.get_by_role('button', name='Go').first`); ChainRejected otherwise."""
    if not isinstance(expr, str) or len(expr) > MAX_EXPR_CHARS:
        _reject("not a short string")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")                 # invalid-escape SyntaxWarnings are noise here
            tree = ast.parse(expr.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return _reject("not a valid Python expression")
    return _py_chain(tree.body, 0)


def parse_chain(expr: str, language: str) -> Chain:
    """Parse for `language` ('python' reads Python, anything else TS / JS); ChainRejected if it is not a plain chain."""
    return parse_py_chain(expr) if language == "python" else parse_ts_chain(expr)


def _encode(value: Any) -> Any:
    if isinstance(value, Regex):
        return {"$re": [value.source, value.flags]}
    if isinstance(value, Chain):
        return {"$loc": chain_to_data(value)}
    return value


def chain_to_data(chain: Chain) -> List[Dict[str, Any]]:
    """The chain as JSON-ready data: [{m: method, a: args, o: options, p: is-property}, ...]."""
    return [{"m": s.method, "a": [_encode(a) for a in s.args], "o": {k: _encode(v) for k, v in s.options.items()},
             "p": s.prop} for s in chain.steps]


def chain_to_json(chain: Chain) -> str:
    """`chain_to_data` as compact ASCII JSON: the only form of the chain meant to leave this module as data."""
    return json.dumps(chain_to_data(chain), separators=(",", ":"))


def try_parse(expr: str, language: str) -> Optional[Chain]:
    """The parsed chain, or None when `expr` is not a plain locator chain."""
    try:
        return parse_chain(expr, language)
    except ChainRejected:
        return None
