"""
===============================================================================
Uniqueness (does a locator point at exactly one listed element?)
===============================================================================
A repair that matches several elements trades a "not found" for a strict-mode violation (a live run once turned
`getByRole('button', {name:'Close'})` into `button:has-text("Close")`, which matched 5 elements). The service has no
browser, so before it writes or accepts a locator it counts from evidence it already holds, strongest first:

    1. Static: a bare-tag selector (optionally with :has-text / :text) is ambiguous by construction
       (`button:has-text("Close")`).
    2. Aria snapshot: for getByRole / get_by_role, count the matching nodes in the snapshot the client saved at the
       failure (role + name, exact / substring / regex, with .first() / .last() / .nth()). Unsupported expressions are
       "unknown", not "ok".
    3. Inventory: the element list the client collected (inventory.py) answers `count` and `matched` for the locator
       kinds it can resolve without a DOM.

`ambiguity()` returns the reason a locator is ambiguous (more than one match), or None when it is unique OR unknown.
A `.first()` / `.nth()` locator is unique by construction and passes. `missing()` is the other half: a locator that
matches zero listed elements is rejected, so a heal cannot swap one guess for another.

Features:
    ✓ Static, aria-snapshot and inventory counting, each optional; nothing here touches a page, a browser or the
      network
    ✓ aria_count honors getByRole's `level` (read from the snapshot's `[level=2]`) and reads `exact` / `level` only outside the
      call's quoted strings, so a name that holds the words "level=3" or "exact=True" is a name
    ✓ aria_count reads role nodes and names only to count them; text after a node's colon (which can be an input's
      value) is used only to name a row-like node and is never returned or stored
    ✓ `Uniqueness(live=None)` accepts a counter a caller supplies (a client that has a real page can verify its own
      healed locator); the service itself never passes one
    ✓ A regex in a locator (a bare /.../flags token or re.compile(...)) is run only by rx, a linear-time engine; one
      it cannot run is "unknown". A quoted string is never a regex
    ✓ Refusal reasons that help the next attempt: the listed element whose id or name is a missing test id, and the
      accessible names the page has under a role that matched nothing

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Protocol

from . import rx
from .bounds import MAX_ARIA_CHARS, MAX_ARIA_LINE_CHARS, MAX_EXPR_CHARS, lines_ok, too_long
from .failing import CALL_WINDOW, balanced_end
from .locator_steps import try_parse
from .locators import LOCATOR_SPLIT_RE, strip_strings
from .redact import role_line

if TYPE_CHECKING:  # inventory.py imports this module
    from .inventory import Inventory

# one line of an aria snapshot: indent, role, quoted name, then anything after the colon
_ROLE_RE = re.compile(r"^(\s*)- ([a-z]+)\b(?: \"((?:[^\"\\]|\\.)*)\")?[^:]*?(?::\s*(.*))?$")

_BARE_TAGS = {"button", "a", "input", "div", "span", "li", "td", "tr", "th", "p", "label", "img", "select",
              "textarea", "h1", "h2", "h3", "h4", "h5", "h6", "form", "ul", "ol", "table", "section", "nav", "i", "b"}
_BARE_RE = re.compile(r"^\s*([a-zA-Z][\w-]*)\s*(?::(?:has-text|text|text-is)\(.*\)|:visible)?\s*$")
_CSS_ARG_RE = re.compile(
    r"""(?:locator|frameLocator|frame_locator)\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")

_NAME_CAP = 500              # characters of content kept per built name


class LiveCounter(Protocol):
    """Counts how many elements a locator expression matches on a live page; None when it cannot tell."""

    def count(self, expr: str, language: str, route: str) -> Optional[int]:
        """Matches of `expr` on `route` (a path or URL), or None for unknown."""
        ...


def normalize_space(s: str) -> str:
    """Collapse every run of whitespace to one space and trim. Case is kept (elements.norm_text also case-folds)."""
    return re.sub(r"\s+", " ", s).strip()


def _first_call_start(expr: str) -> int:
    """Where the first locator call of the chain begins (just after its dot), or -1."""
    first = LOCATOR_SPLIT_RE.search(expr)
    return first.start() + 1 if first else -1


def broad_reason(expr: str) -> Optional[str]:
    """A bare-tag selector (with or without :has-text) matches every such element on the page. Only the first locator
    call of the chain is looked at: a call after another one is already scoped by it."""
    if too_long(expr):
        return None
    m = _CSS_ARG_RE.search(expr)
    if m is None or m.start() != _first_call_start(expr):
        return None
    css = m.group(1) if m.group(1) is not None else m.group(2)
    b = _BARE_RE.match(css)
    if b and b.group(1).lower() in _BARE_TAGS:
        return (f"selector {css!r} is a bare <{b.group(1).lower()}> selector and matches every such element; "
                "scope it to a dialog/section, or use an id, test id, placeholder or role+name")
    return None


# ---- failure-snapshot counting (getByRole) ----------------------------------------------------
# the head of a getByRole / get_by_role call: the role literal. The rest of the call is found by balanced_end, never by
# a lazy pattern (which was quadratic in the expression's length)
_ROLE_HEAD_RE = re.compile(r"""(?:getByRole|get_by_role)\(\s*(['"])(\w+)\1\s*""")
_NAME_RE = re.compile(r"""name\s*[:=]\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|/((?:[^/\\]|\\.)+)/([a-z]*))""")
_EXACT_RE = re.compile(r"exact\s*[:=]\s*(?:true|True)")
_LEVEL_RE = re.compile(r"\blevel\s*[:=]\s*(\d{1,2})\b")
# a heading's line in the snapshot: its quoted name, then the attributes Playwright prints after it ([level=2])
_HEADING_LEVEL_RE = re.compile(r"^\s*- heading\b(?: \"(?:[^\"\\]|\\.)*\")?[^:]*?\[level=(\d{1,2})\]")
_NTH_RE = re.compile(r"\.nth\(\s*(\d+)\s*\)")
_NARROWING_TAIL_RE = re.compile(r"(?:\.(?:first|last)(?:\(\))?|\.nth\(\s*\d+\s*\))*")

# roles whose accessible name comes from their content: an aria snapshot lists that content as children, not as the
# node's name, so an unnamed one is named here from its descendants, as the browser does. Author-named roles (group,
# region, listitem, article) are not: their snapshot name is the whole truth.
_NAME_FROM_CONTENT = {"row", "cell", "gridcell", "treeitem", "columnheader", "rowheader"}


@dataclass
class _Pending:
    """An open unnamed name-from-content node: its name is built from the text of the nodes beneath it."""
    indent: int
    index: int                  # position of the node in the output list
    parts: List[str]
    length: int                 # characters collected so far
    open: bool = True


def role_level(opts: str) -> Optional[int]:
    """The `level` a getByRole call asks for (`level=2`, `level: 2`), read outside its quoted strings: a name that holds the
    words "level=3" is a name. None when the call names no level."""
    m = _LEVEL_RE.search(strip_strings(opts))
    return int(m.group(1)) if m else None


def _nodes(yaml_text: str) -> List[Dict[str, str]]:
    """Role nodes of an aria snapshot. One pass with a stack of open unnamed name-from-content nodes: each node's own
    name / text is appended to every open ancestor that is still short of the cap (linear in the snapshot). A heading
    node also carries its `level` (as text; "" when the snapshot prints none)."""
    out: List[Dict[str, str]] = []
    open_: List[_Pending] = []
    hungry: List[_Pending] = []             # the open entries still under the cap: only these are appended to
    for line in yaml_text.splitlines():
        m = _ROLE_RE.match(role_line(line))
        if not m:
            continue
        indent, role, name = len(m.group(1)), m.group(2), m.group(3) or ""
        inline = (m.group(4) or "").strip().strip('"')
        while open_ and open_[-1].indent >= indent:
            done = open_.pop()
            out[done.index]["name"] = " ".join(done.parts)[:_NAME_CAP]
            done.open = False                   # closed: dropped from `hungry` on its next pass
        text = [x for x in (name, inline) if x]
        if text and hungry:
            add = sum(len(x) + 1 for x in text)
            for pending in hungry:
                pending.parts.extend(text)
                pending.length += add
            hungry = [p for p in hungry if p.open and p.length < _NAME_CAP]   # full or closed: never visited again
        level = _HEADING_LEVEL_RE.match(role_line(line)) if role == "heading" else None
        out.append({"role": role, "name": name, "level": level.group(1) if level else ""})
        if not name and role in _NAME_FROM_CONTENT:
            entry = _Pending(indent, len(out) - 1, [inline] if inline else [], len(inline))
            open_.append(entry)
            if entry.length < _NAME_CAP:
                hungry.append(entry)
    for pending in open_:
        out[pending.index]["name"] = " ".join(pending.parts)[:_NAME_CAP]
    return out


def aria_ok(yaml_text: str) -> bool:
    """Whether an aria snapshot is within its size caps (a snapshot over them is not evaluated at all)."""
    return len(yaml_text) <= MAX_ARIA_CHARS and lines_ok(yaml_text, MAX_ARIA_LINE_CHARS)


def _role_call(expr: str):
    """(role, option text, tail after the call) of the expression's first call when it is a lone getByRole, else None."""
    m = _ROLE_HEAD_RE.search(expr)
    if not m or m.start() != _first_call_start(expr):
        return None                                           # a role call after a scope: needs the DOM
    end = balanced_end(expr, m.end() - len(m.group(0)) + m.group(0).index("("), CALL_WINDOW)
    if end < 0:
        return None
    inner = expr[m.end():end - 1]
    if inner.strip() and not inner.startswith(","):
        return None
    tail = expr[end:]
    if tail.strip() and not tail.lstrip().startswith("."):
        return None
    return m.group(2), inner[1:].strip(), tail


def aria_count(expr: str, yaml_text: str) -> Optional[int]:
    """How many nodes in the failure snapshot a getByRole expression matches; None if unsupported, or if the
    expression or the snapshot is over its size cap (nothing is cut: "cannot evaluate")."""
    if not yaml_text or too_long(expr, MAX_EXPR_CHARS) or not aria_ok(yaml_text):
        return None
    call = _role_call(expr)
    if call is None:
        return None
    role, opts, tail = call
    if tail.strip() and not _NARROWING_TAIL_RE.fullmatch(tail.strip()):
        return None                                           # .filter(), .locator(), ... need the DOM
    if "re.compile" in opts:
        return None                                           # a Python regex name: not evaluated here
    nm = _NAME_RE.search(opts)
    exact = bool(_EXACT_RE.search(strip_strings(opts)))       # an option, never the words of a name that look like one
    level = role_level(opts)
    nodes = [n for n in _nodes(yaml_text) if n["role"] == role]
    if level is not None:
        nodes = [n for n in nodes if n["level"] == str(level)]
    if nm:
        if nm.group(3) is not None:
            try:
                with rx.work_budget():
                    nodes = [n for n in nodes if rx.search(nm.group(3), nm.group(4), n["name"])]
            except rx.CannotEvaluate:
                return None                                   # a pattern RE2 will not run: no claim
        else:
            want = normalize_space(nm.group(1) if nm.group(1) is not None else nm.group(2))
            nodes = [n for n in nodes
                     if (normalize_space(n["name"]) == want if exact
                         else want.lower() in normalize_space(n["name"]).lower())]
    count = len(nodes)
    for t in re.findall(r"\.(first|last|nth\(\s*\d+\s*\))", tail):
        k = _NTH_RE.search("." + t)
        count = min(count, 1) if k is None else (1 if count > int(k.group(1)) else 0)
    return count


def ts_expr_is_safe(expr: str) -> bool:
    """True when `expr` is a plain TS / JS locator chain on `page` (see locator_steps.parse_ts_chain): literal
    arguments only. It is the same parser the rest of the service uses, so nothing "safe" is ever evaluated."""
    return try_parse(expr, "typescript") is not None


def py_expr_is_safe(expr: str) -> bool:
    """True when `expr` is a plain Python locator chain on `page` (see locator_steps.parse_py_chain)."""
    return try_parse(expr, "python") is not None


_TEST_ID_CALL_RE = re.compile(r"""(?:getByTestId|get_by_test_id)\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")


def _listed_instead(expr: str, inventory: "Inventory") -> str:
    """For a test id nothing carries: the listed element whose id or name attribute is that value, and its locator."""
    m = _TEST_ID_CALL_RE.search(expr)
    if m is None:
        return ""
    want = m.group(1) if m.group(1) is not None else m.group(2)
    for el in inventory.elements:
        if el.get("id") == want:
            return f". No element has the test id {want!r}; id='{want}' is listed: use locator('#{want}')"
        if el.get("name_attr") == want:
            return (f". No element has the test id {want!r}; name='{want}' is listed: use "
                    f"locator('{el.get('tag', '')}[name=\"{want}\"]')")
    return ""


# one get_by_role / getByRole call with a name and nothing else around it
_ROLE_NAME_CALL_RE = re.compile(
    r"""^\s*[\w$.]+\.(?:get_by_role\(\s*(?P<q1>["'])(?P<r1>[\w-]+)(?P=q1)\s*,\s*name\s*=|"""
    r"""getByRole\(\s*(?P<q2>["'])(?P<r2>[\w-]+)(?P=q2)\s*,\s*\{\s*name\s*:)""")
MAX_HINT_NAMES = 6
HINT_NAME_CHARS = 80


def _role_hint(expr: str, inventory: "Inventory") -> str:
    """For a role + name that matches nothing: what the page has under that role (its accessible names, or none)."""
    if too_long(expr) or not inventory.aria or not aria_ok(inventory.aria):
        return ""
    m = _ROLE_NAME_CALL_RE.match(expr)
    if m is None:
        return ""
    role = m.group("r1") or m.group("r2")
    names = [n for n in (re.sub(r"\\(.)", r"\1", g) if g is not None else ""
                         for g in re.findall(r'^[ \t]*- ' + re.escape(role) + r'(?: "((?:[^"\\]|\\.)*)")?',
                                            "\n".join(role_line(x) for x in inventory.aria.splitlines()), re.M))]
    if not names:
        return ""
    shown = ", ".join(repr(n[:HINT_NAME_CHARS]) if n else "no accessible name"
                      for n in dict.fromkeys(names[:MAX_HINT_NAMES]))
    return (f". The page has {len(names)} element(s) with role {role!r} ({shown}); name them as listed, or drop "
            "name= when it has no accessible name")


class Uniqueness:
    """The checks a proposed locator must pass before it replaces a failing one: it must point at something
    (`missing`) and at only one thing (`ambiguity`). Each check is optional."""

    def __init__(self, live: Optional[LiveCounter] = None, static: bool = True, existence: bool = True):
        self.live, self.static, self.existence = live, static, existence

    def missing(self, proposal: str, inventory: Optional["Inventory"] = None) -> Optional[str]:
        """Reason the proposal points at nothing (matches zero elements in the inventory), or None when it matches
        something OR cannot be told. See inventory.Inventory."""
        if not self.existence or inventory is None:
            return None
        n = inventory.count(proposal)
        if n == 0:
            return ("matches no element in the page snapshot: pick a locator for an element that is listed there "
                    "(getByPlaceholder for an input with only a placeholder, a class / id / test id that is shown). "
                    "An attribute the snapshot does not show is absent from the element, so a selector on it "
                    "matches nothing" + _listed_instead(proposal, inventory)
                    + _role_hint(proposal, inventory))
        return None

    def ambiguity(self, proposal: str, language: str, route: str = "/", aria_yaml: str = "",
                  inventory: Optional["Inventory"] = None) -> Optional[str]:
        """Reason the proposal is ambiguous (matches more than one element), or None if unique/unknown."""
        if self.static:
            why = broad_reason(proposal)
            if why:
                return why
            n = aria_count(proposal, aria_yaml)
            if n is not None and n > 1:
                return f"matches {n} elements in the page at the failure: narrow it (exact name, scope, id or test id)"
            n = inventory.count(proposal) if inventory is not None else None
            if n is not None and n > 1:
                return f"matches {n} elements in the page snapshot: narrow it (exact name, scope, id or test id)"
        if self.live is not None:
            n = self.live.count(proposal, language, route)
            if n is not None and n > 1:
                return (f"matches {n} elements on the live page: narrow it "
                        "(scope to the dialog/section, id or test id)")
        return None
