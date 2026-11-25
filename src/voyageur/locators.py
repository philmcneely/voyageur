"""
===============================================================================
Locator Utilities (heal-scope guard)
===============================================================================
Pulls the broken selector out of a failure, finds the exact locator call in the
test source, and validates a replacement. A heal may only swap one locator
expression for another with the SAME receiver; assertions and test intent are
never touched. The same checks run on a locator the service wrote itself, so a
bug in the writer can never hand a client something that is not a plain locator.

Features:
    ✓ Selector extraction from Playwright / pytest failure output
    ✓ Exact locator-call lookup in source (single or double quoted literals)
    ✓ Python (get_by_*) and TypeScript/JavaScript (getBy*) locator calls
    ✓ Replacement validation per language (same receiver, one locator call chain;
      Python via ast, TS/JS via a strict scanner that allows only `.name(...)` chains)
    ✓ Every argument of every call in a chain must be a LITERAL (string, number, bool, null, a TS regex literal or a
      template literal without ${}, arrays / option objects of those, and, as the argument of filter / and / or, a
      nested locator chain that obeys the same rules). A replacement can be built from page content (names, labels,
      attribute values), so an identifier, attribute access, call, f-string, concatenation, spread or comment never
      gets through to be run or written into a test

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import ast
import io
import re
import tokenize
from typing import List, NoReturn, Optional, Set, Tuple

from .bounds import MAX_EXPR_CHARS, too_long

QUOTED_LITERAL = r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")"""   # a '...' or "..." literal: two capture groups
LOCATOR_CALL = r"(?:locator|frameLocator|frame_locator|get_by_\w+|getBy\w+)"
_SELECTOR_RE = re.compile(LOCATOR_CALL + r"\(\s*" + QUOTED_LITERAL)
_WAITING_RE = re.compile(r"waiting for (?:locator\(|locator )?" + QUOTED_LITERAL)
_CALL_RE = re.compile(r"(?<![\w.])[A-Za-z_][\w.]*\." + LOCATOR_CALL + r"\(\s*" + QUOTED_LITERAL + r"\s*(?:,[^()]*)?\)")
# the ".locator(" / ".getByRole(" that ends a receiver
LOCATOR_SPLIT_RE = re.compile(r"\." + LOCATOR_CALL + r"\(")


def first_group(m: "re.Match[str]") -> str:
    """The first capture group of `m` that matched (the literal, whichever quote style it used)."""
    return next(g for g in m.groups() if g is not None)


def broken_selector_from(error: str) -> Optional[str]:
    """First concrete selector literal named in a failure; 'waiting for' wins (Playwright)."""
    m = _WAITING_RE.search(error) or _SELECTOR_RE.search(error)
    return first_group(m) if m else None


def selectors_from(error: str) -> List[str]:
    """All selector literals named in a failure: 'waiting for' hits first, then call literals."""
    seen: List[str] = []
    for rx in (_WAITING_RE, _SELECTOR_RE):
        for m in rx.finditer(error):
            sel = first_group(m)
            if sel not in seen:
                seen.append(sel)
    return seen


def pick_broken(error: str, source: str) -> Optional[Tuple[str, str]]:
    """The (selector, exact call) from the failure that really is a locator call in `source`.

    A call whose own text (minus receiver) is printed in the failure wins, so two
    getByRole('button', ...) calls are told apart by their options. Otherwise the
    first reported selector that exists in the source.
    """
    for m in _CALL_RE.finditer(source):
        tail = LOCATOR_SPLIT_RE.split(m.group(0), 1)
        printed = m.group(0)[len(tail[0]) + 1:]
        if printed and printed in error:
            return first_group(m), m.group(0)
    for sel in selectors_from(error):
        call = find_call(source, sel)
        if call:
            return sel, call
    return None


def find_call(source: str, selector: str) -> Optional[str]:
    """Return the exact locator call expression in source whose literal is `selector`."""
    for m in _CALL_RE.finditer(source):
        if first_group(m) == selector:
            return m.group(0)
    return None


def receiver_of(call: str) -> str:
    """'self.page.locator(...)' -> 'self.page'; 'page.getByRole(...)' -> 'page'."""
    return LOCATOR_SPLIT_RE.split(call, 1)[0]


_TS_BANNED = re.compile(r"\b(expect|await|eval|require|import|function|new|this)\b")
# chain methods that only narrow
_TS_NARROW = {"first", "last", "nth", "filter", "locator", "and", "or", "frameLocator"}
_PY_NARROW = {"first", "last", "nth", "filter", "locator", "and_", "or_", "frame_locator"}
_ACTIONS = {"click", "dblclick", "fill", "type", "press", "check", "uncheck", "hover", "focus", "blur",
            "selectOption", "select_option", "setInputFiles", "set_input_files", "tap", "dragTo", "drag_to", "clear",
            "scrollIntoViewIfNeeded", "scroll_into_view_if_needed", "textContent", "text_content", "innerText",
            "inner_text", "inputValue", "input_value", "count", "isVisible", "is_visible", "waitFor", "wait_for",
            "evaluate"}


def strip_strings(s: str) -> str:
    """`s` with every '...', "..." and `...` literal collapsed to '' so structure can be scanned without their text."""
    return re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`", "''", s)


def other_spelling(name: str, language: str) -> Optional[str]:
    """If `name` is a locator method written the way the OTHER language spells it, how this language spells it."""
    if language in ("typescript", "javascript") and (name.startswith("get_by_") or name == "frame_locator"):
        camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), name)
        return f"{name}() is the Python spelling: TypeScript writes {camel}(...)"
    if language == "python" and (name.startswith("getBy") or name == "frameLocator"):
        snake = re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()
        return f"{name}() is the TypeScript spelling: Python writes {snake}(...)"
    return None


_TOO_LONG = f"longer than {MAX_EXPR_CHARS} characters: not a locator a person would write"


def ts_chain_reason(rest: str, literal: bool = True) -> Optional[str]:
    """Why `rest` (text after the receiver's dot) is not a locator-only chain; None if it is."""
    if too_long(rest):
        return _TOO_LONG
    bare = strip_strings(rest)
    if bare.count("'") % 2 or "`" in rest and "${" in rest:
        return "unbalanced quotes or template interpolation (arguments must be literals)"
    act = re.search(r"\.(" + "|".join(sorted(_ACTIONS)) + r")\(", bare)
    if act:
        return f"includes an action, .{act.group(1)}(): the heal replaces the locator only"
    if any(t in bare for t in (";", "//", "/*", "\n")):
        return "contains a statement separator or comment"
    if "=" in bare:
        return "contains an assignment or arrow"
    banned = _TS_BANNED.search(bare)
    if banned:
        return f"uses {banned.group(0)!r}, which is not a locator"
    depth, names, cur = 0, [], ""
    for c in bare:
        if c in "([{":
            if depth == 0 and c == "(":
                names.append(cur)
            depth += 1
            cur = ""
        elif c in ")]}":
            depth -= 1
            if depth < 0:
                return "unbalanced brackets"
        elif depth == 0:
            if c == ".":
                cur = ""
            elif c.isalnum() or c in "_$":
                cur += c
            else:
                return f"unexpected {c!r} outside a call"
    if depth != 0 or not bare.endswith(")") or not names:
        return "not a call chain"
    if not (names[0] in ("locator", "frameLocator") or names[0].startswith("getBy")):
        return other_spelling(names[0], "typescript") or f"first call {names[0]!r} is not a locator"
    for n in names[1:]:
        if n in _ACTIONS:
            return f"includes an action, .{n}(): the heal replaces the locator only"
        if not (n in _TS_NARROW or n.startswith("getBy")):
            return other_spelling(n, "typescript") or f"chain method .{n}() is not a locator-narrowing call"
    return _ts_args_problem(rest) if literal else None


_NEEDS_ARGUMENT = {"locator", "frameLocator", "frame_locator", "filter", "and", "or", "and_", "or_"}


def _needs_argument(name: str) -> bool:
    """Locator methods that cannot be called bare: Playwright would build a locator for `undefined`."""
    return name in _NEEDS_ARGUMENT or name.startswith(("getBy", "get_by_"))


_LITERAL = "arguments must be literals (a string, number or bool; option values likewise)"
_NARROWING_ARG_PARENTS = {"filter", "and_", "or_", "and", "or"}     # these take a locator as an argument
_TS_WORDS = {"true", "false", "null", "undefined"}


def _py_lit(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, int, float, bool, type(None)))
    return (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, (int, float)))


def _dotted(node: ast.AST) -> bool:
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name)


def _py_node_reason(node: ast.AST, literal: bool) -> Optional[str]:
    attrs: List[str] = []
    while True:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attrs.append(node.func.attr)
            if _needs_argument(node.func.attr) and not node.args and not node.keywords:
                return f"{node.func.attr}() needs an argument"
            if literal:
                vals = list(node.args) + [k.value for k in node.keywords]
                if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
                    return _LITERAL + ": no * or ** unpacking"
                for v in vals:
                    if _py_lit(v):
                        continue
                    if isinstance(v, ast.Call) and node.func.attr in _NARROWING_ARG_PARENTS:
                        why = _py_node_reason(v, literal)          # filter(has=page.get_by_text("x")): a nested chain
                        if why:
                            return why
                        continue
                    return _LITERAL
            node = node.func.value
        elif isinstance(node, ast.Attribute) and node.attr in ("first", "last"):   # property chain members
            attrs.append(node.attr)
            node = node.value
        else:
            break
    attrs.reverse()
    if not attrs:
        return "not a call chain"
    if not _dotted(node):
        return "the chain does not start at a plain receiver"
    if not (attrs[0] in ("locator", "frame_locator") or attrs[0].startswith("get_by_")):
        return other_spelling(attrs[0], "python") or f"first call {attrs[0]!r} is not a locator"
    for a in attrs[1:]:
        if a in _ACTIONS:
            return f"includes an action, .{a}(): the heal replaces the locator only"
        if not (a in _PY_NARROW or a.startswith("get_by_")):
            return other_spelling(a, "python") or f"chain member .{a} is not a locator-narrowing call"
    return None


def py_chain_reason(proposal: str, literal: bool = True) -> Optional[str]:
    """Why `proposal` is not a Python locator-only chain with literal arguments; None if it is one."""
    if too_long(proposal):
        return _TOO_LONG
    try:
        tree = ast.parse(proposal, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):    # ValueError: a lone surrogate, a NUL byte
        return "not a valid Python expression"
    try:
        if any(t.type == tokenize.COMMENT for t in tokenize.generate_tokens(io.StringIO(proposal).readline)):
            return "contains a comment (it would swallow what follows the locator)"
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError, RecursionError, MemoryError):
        return "not a valid Python expression"
    return _py_node_reason(tree.body, literal)


class _NotLiteral(Exception):
    """An argument of a TS locator chain is not a literal."""


class _TsArgs:
    """Recursive-descent check that a TS/JS locator chain's arguments are literals. Grammar:
        chain := call ('.' call)*            call := ident '(' [value (',' value)* [',']] ')'
        value := string | template-without-${} | /regex/flags | number | true | false | null | undefined
               | '[' values ']' | '{' (ident|string) ':' value, ... '}'
               | locator-chain (only as an argument of filter / and / or)
    Every method takes and returns the index into the text; a failure raises _NotLiteral."""

    IDENT = re.compile(r"[A-Za-z_$][\w$]*")
    NUM = re.compile(r"-?\d+(?:\.\d+)?")

    def __init__(self, s: str):
        self.s, self.n = s, len(s)

    def fail(self, why: str = _LITERAL) -> NoReturn:
        """Stop the check, saying why."""
        raise _NotLiteral(why)

    def ws(self, i: int) -> int:
        """Index of the first non-blank character at or after `i`."""
        while i < self.n and self.s[i] in " \t":
            i += 1
        return i

    def check(self) -> None:
        """Raise _NotLiteral unless the whole text is one locator chain with literal arguments."""
        i = self.chain(0, False)
        if self.ws(i) != self.n:
            self.fail("text after the locator chain")

    def chain(self, i: int, nested: bool) -> int:
        """End of the `call ('.' call)*` chain starting at `i`."""
        i = self.call(i, nested)
        while True:
            j = self.ws(i)
            if j < self.n and self.s[j] == ".":
                i = self.call(self.ws(j + 1), nested)
            else:
                return i

    def call(self, i: int, nested: bool) -> int:
        """End of one `name(args)` call starting at `i`."""
        m = self.IDENT.match(self.s, i)
        if not m:
            self.fail("not a call chain")
        i = self.ws(m.end())
        if i >= self.n or self.s[i] != "(":
            self.fail("not a call chain")
        name = m.group(0)
        i = self.ws(i + 1)
        if i < self.n and self.s[i] == ")" and _needs_argument(name):
            self.fail(f"{name}() needs an argument")
        while True:
            if i < self.n and self.s[i] == ")":
                return i + 1
            i = self.ws(self.value(i, name))
            if i < self.n and self.s[i] == ",":
                i = self.ws(i + 1)
                continue
            if i < self.n and self.s[i] == ")":
                return i + 1
            self.fail()

    def quoted(self, i: int, q: str) -> int:
        """End of the string / template literal opened by quote `q` at `i` (no `${` interpolation)."""
        j = i + 1
        while j < self.n and self.s[j] != q:
            if self.s[j] == "\\":
                j += 1
            elif q == "`" and self.s[j] == "$" and self.s[j + 1:j + 2] == "{":
                self.fail(_LITERAL + " (no template interpolation)")
            j += 1
        if j >= self.n:
            self.fail("unterminated string")
        return j + 1

    def regex(self, i: int) -> int:
        """End of the /regex/flags literal starting at `i`."""
        j, in_cls = i + 1, False
        while j < self.n and (in_cls or self.s[j] != "/"):
            if self.s[j] == "\\":
                j += 1
            elif self.s[j] == "[":
                in_cls = True
            elif self.s[j] == "]":
                in_cls = False
            j += 1
        if j >= self.n or j == i + 1:
            self.fail("bad regex literal")
        j += 1
        while j < self.n and self.s[j].isalpha():
            j += 1
        return j

    def value(self, i: int, parent: str) -> int:
        """End of one argument value starting at `i`; `parent` is the method it is an argument of."""
        if i >= self.n:
            self.fail()
        c = self.s[i]
        if c in "'\"`":
            return self.quoted(i, c)
        if c == "/":
            return self.regex(i)
        if c == "[":
            i = self.ws(i + 1)
            while i < self.n and self.s[i] != "]":
                i = self.ws(self.value(i, parent))
                if i < self.n and self.s[i] == ",":
                    i = self.ws(i + 1)
                elif i < self.n and self.s[i] != "]":
                    self.fail()
            if i >= self.n:
                self.fail()
            return i + 1
        if c == "{":
            i = self.ws(i + 1)
            while i < self.n and self.s[i] != "}":
                m = self.IDENT.match(self.s, i)
                if m:
                    i = m.end()
                elif self.s[i] in "'\"":
                    i = self.quoted(i, self.s[i])
                else:
                    self.fail(_LITERAL + " (no spread or computed keys)")
                i = self.ws(i)
                if i >= self.n or self.s[i] != ":":
                    self.fail()
                i = self.ws(self.value(self.ws(i + 1), parent))
                if i < self.n and self.s[i] == ",":
                    i = self.ws(i + 1)
                elif i < self.n and self.s[i] != "}":
                    self.fail()
            if i >= self.n:
                self.fail()
            return i + 1
        m = self.NUM.match(self.s, i)
        if m:
            return m.end()
        m = self.IDENT.match(self.s, i)
        if not m:
            self.fail()
        if m.group(0) in _TS_WORDS and not self.s[m.end():].lstrip().startswith((".", "(")):
            return m.end()
        if parent not in _NARROWING_ARG_PARENTS:
            self.fail()
        j = i                                       # a nested locator: receiver path, then locator / getBy* calls
        while True:
            m = self.IDENT.match(self.s, j)
            if not m:
                self.fail()
            k = self.ws(m.end())
            if k < self.n and self.s[k] == "(":
                if not (m.group(0) in ("locator", "frameLocator") or m.group(0).startswith("getBy")):
                    self.fail("a nested chain must start at a locator")
                return self.chain(j, True)
            if k < self.n and self.s[k] == ".":
                j = self.ws(k + 1)
                continue
            self.fail()


def _ts_args_problem(rest: str) -> Optional[str]:
    try:
        _TsArgs(rest).check()
    except _NotLiteral as e:
        return str(e)
    except IndexError:
        return _LITERAL
    return None


# a selector string handed to locator() / frame_locator() / frameLocator(), and an attribute the page spells data-x
# written data_x (the listing's internal key): `[data_qa=..]` in CSS, `@data_qa` in XPath
_SELECTOR_ARG_RE = re.compile(
    r"""\b(?:locator|frame_locator|frameLocator)\(\s*(?P<q>["'`])(?P<sel>(?:\\.|(?!(?P=q))[^\\])*)(?P=q)""")
_UNDERSCORE_ATTR_RE = re.compile(r"(?:\[\s*|@)(data_[A-Za-z0-9_]+)(?=\s*(?:[~|^$*]?=|\]|\)|$))")


def underscore_attribute(proposal: str) -> Optional[str]:
    """The reason a selector in `proposal` names a data-* attribute with underscores (the page's attribute is
    data-x, never data_x), or None."""
    if too_long(proposal):
        return None
    for m in _SELECTOR_ARG_RE.finditer(proposal):
        found = _UNDERSCORE_ATTR_RE.search(m.group("sel"))
        if found:
            real = "data-" + found.group(1)[5:].replace("_", "-")
            return (f"the attribute is {real}, not {found.group(1)}: write [{real}=...] "
                    f"(the page's attributes are spelled with hyphens)")
    return None


def validate_replacement(call: str, proposal: str, language: str = "python",
                         known_receivers: Optional[Set[str]] = None) -> Optional[str]:
    """None if `proposal` may replace `call`; otherwise the specific reason it may not.

    Allowed: any locator strategy on the same element (getByRole -> locator('#id') -> getByTestId ...),
    narrowing (.first/.nth/.filter/.locator), frame locators. The receiver must be the original's or a handle the
    test already uses for locators (so `this.modal.getByRole(..)` may become `this.page.locator('#m').getByRole(..)`).
    Not allowed: no change, several lines, actions or statements, a non-locator first call."""
    if not proposal or not proposal.strip():
        return "empty proposal"
    if too_long(proposal) or too_long(call):
        return _TOO_LONG
    if proposal == call:
        return "identical to the failing locator (no change)"
    if "\n" in proposal:
        return "more than one line"
    if not LOCATOR_SPLIT_RE.search(proposal):
        return "not a locator expression (no locator() / getBy*() call)"
    recv = receiver_of(call)
    got = LOCATOR_SPLIT_RE.split(proposal, 1)[0]
    if got != recv and not (known_receivers and got in known_receivers):
        return f"different receiver {got!r} (expected {recv!r} or a locator handle already used in this test)"
    if not proposal.startswith(got + "."):
        return "not a method call on the receiver"
    why = underscore_attribute(proposal)
    if why:
        return why
    if language in ("typescript", "javascript"):
        return ts_chain_reason(proposal[len(got) + 1:])
    return py_chain_reason(proposal)


def is_valid_replacement(call: str, proposal: str, language: str = "python",
                         known_receivers: Optional[Set[str]] = None) -> bool:
    """True when `validate_replacement` finds nothing wrong with the proposal."""
    return validate_replacement(call, proposal, language, known_receivers) is None
