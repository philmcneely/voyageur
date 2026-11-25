"""
===============================================================================
Element Inventory (does a locator match anything on the page?)
===============================================================================
A repair that points at nothing just swaps one guess for another (a heal changed a placeholder locator to
`input[type="text"]`; the real input was `<input class="new-todo" placeholder="...">` with NO type attribute, so it
matched nothing). The client sends an inventory: the elements a locator can target, as dicts with every attribute
present (absent ones omitted), plus the page's aria snapshot. The service has no browser, so this module is its
uniqueness oracle: it evaluates a locator against that inventory and says how many elements it would match, and which
ones.

    count(expr) -> int   matches in the inventory (0 means the locator points at nothing)
                 -> None unknown: the expression, or the part of the page it targets, is not covered
    matched(expr) -> the listed elements a single locator call matches, or None when it cannot be told

Conclusive only where the inventory is authoritative, and silent otherwise (unknown never rejects):
    placeholder / test id / alt / title / label   from the inventory (needs a complete listing)
    CSS: one compound selector (tag, #id, .class, [attr op value]) whose tag is an element
         the inventory lists (input, button, select, textarea, a) or that carries an #id / [data-testid] /
         [role] / [placeholder] / [name] / [type]; bare `.class` and anything with a combinator or pseudo-class
         is unknown
    getByRole / getByText                         from the aria snapshot, getByText also from element text
Chains: the base call decides zero; `.first()` / `.last()` / `.nth(n)` narrow a non-zero base.

Features:
    ✓ Inventory(elements, complete, aria): built from plain dicts; entries that are not dicts, and values that are not
      strings (or `hidden` / `box`), are dropped, so a malformed request cannot reach the matching code
    ✓ Inventory.count: placeholder, test id, label, alt, title, role, text, compound CSS
    ✓ Inventory.matched: WHICH listed elements a single locator call matches (text included), so a written locator can
      be proven to resolve to the element that was picked
    ✓ Absent attributes stay absent: `[type="text"]` does not match an input with no type
    ✓ copy_more > 0 (the probe left copy-holding elements out): a text count that reads the listing, and which elements getByText
      matches, are unknown; so is a substring text match of a copy_only element (copy the probe withheld could contain it)
    ✓ `label[for="x"]` is counted (the probe lists every label with a `for`); getByText matches the listed copy of an element,
      never the control a label names
    ✓ A locator's own regex text is bounded and a malformed one is "no match", never an error

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from .failing import balanced_end
from .locators import LOCATOR_SPLIT_RE
from . import rx
from .bounds import MAX_EXPR_CHARS, MAX_FIELD_CHARS, too_long
from .redact import redact_typed_values, role_line
from .uniqueness import _ROLE_RE, aria_count, aria_ok, normalize_space

_LISTED_TAGS = {"input", "button", "select", "textarea", "a"}
_ATTR_KEYS = {"type": "type", "role": "role", "id": "id", "class": "class", "name": "name_attr",
              "placeholder": "placeholder", "title": "title", "alt": "alt", "for": "for_attr", "data-testid": "testid",
              "data-test": "testid", "aria-label": "name", "data-qa": "data_qa", "data-cy": "data_cy"}
_SCOPE_ATTRS = {"data-testid", "data-test", "data-qa", "data-cy", "role", "placeholder", "name", "type", "id"}
_COMPOUND_RE = re.compile(
    r"""^(?P<tag>[a-zA-Z][\w-]*|\*)?(?P<rest>(?:#[\w-]+|\.[\w-]+|\[[^\]]+\])*)$""")
_PART_RE = re.compile(
    r"""#([\w-]+)|\.([\w-]+)|\[\s*([\w-]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+)))?\s*\]""")


Condition = Tuple[str, str, Optional[str]]          # (attribute, operator, value) of one part of a compound selector


def compound_of(css: str) -> Optional[Tuple[str, List[Condition], bool]]:
    """(tag, conditions, scoped) of a single compound selector (`tag#id.class[attr^=v]`) the listing can read, or None.
    `scoped` is False when the listing may not hold every element the selector matches (a bare class or tag)."""
    m = _COMPOUND_RE.match(css.strip())
    if not m:
        return None
    tag = (m.group("tag") or "").lower()
    conds: List[Condition] = []
    scoped = tag in _LISTED_TAGS
    for pm in _PART_RE.finditer(m.group("rest") or ""):
        if pm.group(1):
            conds.append(("id", "=", pm.group(1)))
            scoped = True
        elif pm.group(2):
            conds.append(("class", "~=", pm.group(2)))
        else:
            attr = pm.group(3)
            if attr not in _ATTR_KEYS and not attr.startswith("data-"):
                return None                                  # an attribute the inventory does not carry
            val = next((g for g in (pm.group(5), pm.group(6), pm.group(7)) if g is not None), None)
            conds.append((attr, pm.group(4) or "exists", val))
            # the probe lists every label with a `for`, so a count over `label[for=...]` is complete
            scoped = scoped or attr in _SCOPE_ATTRS or attr.startswith("data-") or (attr == "for" and tag == "label")
    return tag, conds, scoped


def compound_holds(tag: str, conds: List[Condition], el: Dict[str, Any]) -> Optional[bool]:
    """Whether the listed element `el` matches a compound read by compound_of; None for an operator it cannot judge."""
    if tag and tag != "*" and el.get("tag") != tag:
        return False
    for attr, op, val in conds:
        have = el.get(_ATTR_KEYS.get(attr) or attr.replace("-", "_"), "")
        if op == "exists":
            ok = bool(have)
        elif op == "=":
            ok = have == val
        elif op == "~=":
            ok = val in have.split()
        elif op == "^=":
            ok = have.startswith(val)
        elif op == "$=":
            ok = have.endswith(val)
        elif op == "*=":
            ok = val in have
        else:
            return None
        if not ok:
            return False
    return True


_PY_COMPILE_RE = re.compile(
    r"""\s*re\.compile\(\s*r?(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")\s*(?:,\s*(re\.(?:I|IGNORECASE)|0|re\.[A-Z]+(?:\s*\|\s*re\.[A-Z]+)*))?\s*\)""")


_ESCAPE_RE = re.compile(r"\\(?:u\{([0-9a-fA-F]{1,6})\}|u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8})|(.))", re.S)
_SIMPLE = {"n": "\n", "r": "\r", "t": "\t"}


def _unescape(lit: str) -> str:
    """The text a quoted literal holds: \\uXXXX, \\u{X}, \\UXXXXXXXX, \\n, \\r, \\t and an escaped quote or backslash
    are decoded (the writers escape hidden characters and quotes this way); any other escape keeps its character."""
    def one(m: "re.Match[str]") -> str:
        hexa = m.group(1) or m.group(2) or m.group(3)
        if hexa:
            n = int(hexa, 16)
            return chr(n) if n <= 0x10FFFF else m.group(0)
        return _SIMPLE.get(m.group(4), m.group(4))
    return _ESCAPE_RE.sub(one, lit) if "\\" in lit else lit


def _lit_and_opts(args: str) -> Tuple[Optional[str], str, bool]:
    """(first literal, options text, exact?) of a call's argument text. A quoted string is a plain str; only a bare
    `/body/flags` token or a Python `re.compile("body")` is returned as an rx.RegexLit (the only things ever run as a
    pattern)."""
    if too_long(args):
        return None, args, False                     # over the expression cap: not read
    m = re.match(r"""\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|/((?:[^/\\\n]|\\.)+)/([a-z]*))""", args)
    pm = None if m else _PY_COMPILE_RE.match(args)
    if not m and not pm:
        return None, args, False
    if m:
        lit = m.group(1) if m.group(1) is not None else m.group(2)
        if lit is not None:
            lit = _unescape(lit)
        else:
            lit = rx.RegexLit("/" + m.group(3) + "/" + m.group(4))
        end = m.end()
    else:
        body = pm.group(1) if pm.group(1) is not None else pm.group(2)
        lit = rx.RegexLit("/" + body + "/" + ("i" if pm.group(3) and "I" in pm.group(3) else ""))
        end = pm.end()
    rest = args[end:]
    return lit, rest, bool(re.search(r"exact\s*[:=]\s*(?:true|True)", rest))


def _match_text(want: str, have: str, exact: bool) -> bool:
    """Whether `have` matches `want`. Raises rx.CannotEvaluate for a regex that may not be run."""
    if isinstance(want, rx.RegexLit):
        body, flags = rx.split_literal(want)
        return rx.search(body, flags, have)
    have_n, want_n = normalize_space(have), normalize_space(want)
    return have_n == want_n if exact else want_n.lower() in have_n.lower()


def _innermost(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`hits` (listed elements whose copy matched) without the wrappers the probe marked (`copy_wrap`, said by the DOM): getByText
    matches the smallest element holding the text, so a wrapper of one text is not a match of its own."""
    return [h for h in hits if not h.get("copy_wrap")]


_WITHHELD_ADDRESS = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+|(?:\d[ .-]?){6,}")


def _withheld_shape(want: Any) -> bool:
    """True when `want` (a locator's text) is copy the probe would withhold from a listing: over 80 characters, an address, a run
    of six digits, a token-like word. A listing that does not hold it says nothing about whether the page does."""
    if not isinstance(want, str):
        return False
    # the probe measures length as JavaScript does, in UTF-16 units
    return (len(want.encode("utf-16-le")) // 2 > 80 or bool(_WITHHELD_ADDRESS.search(want))
            or any(len(w) >= 24 and re.search(r"\d", w) and re.search(r"[A-Za-z]", w) for w in want.split()))


def _count(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def _clean(el: Any) -> Optional[Dict[str, Any]]:
    """A copy of one inventory entry holding only what the matching code can use: string values, the `hidden` flag
    and the `box` dict. None for an entry that is not a dict."""
    if not isinstance(el, dict):
        return None
    out: Dict[str, Any] = {}
    for k, v in el.items():
        if not isinstance(k, str):
            continue
        if k in ("hidden", "top_at_center", "hit", "pw_cut", "copy_only", "copy_cut", "copy_wrap"):
            if isinstance(v, bool):
                out[k] = v
        elif k == "box":
            if isinstance(v, dict):
                out[k] = v
        elif isinstance(v, str):
            out[k] = v
    return out


class Inventory:
    """The elements a locator can target, as the client collected them, plus the failure-time aria snapshot.
    `complete` is False when the list was cut short, which turns "matches nothing" into "unknown" for the
    attribute-based lookups."""

    def __init__(self, elements: List[Dict[str, Any]], complete: bool, aria: str = "", copy_more: int = 0, withheld: Optional[int] = 0,
                 withheld_data: Optional[int] = 0):
        cleaned = [c for c in (_clean(e) for e in elements) if c is not None]
        # copy-holding elements the probe left out for want of room: the listing's text is then not all of the page's, and no
        # count that reads it can be told (the request's count, or any copy_only entry saying the list was cut)
        self.copy_more = max(_count(copy_more), 1 if any(c.get("copy_cut") for c in cleaned) else 0)
        # visible copy the probe's filters kept out (too long, an address, digits, inside a control or a data widget); the
        # second count is the part inside a control or data widget, short copy that can equal a text locator's exactly
        self.withheld, self.withheld_data = _count(withheld), _count(withheld_data)
        # a client that sent no counts (None) with a listing of copy_only items is an older one: what the probe withheld is
        # not known, and not knowing is not "nothing"
        if (withheld is None or withheld_data is None) and any(c.get("copy_only") for c in cleaned):
            self.withheld, self.withheld_data = max(self.withheld, 1), max(self.withheld_data, 1)
        # a field over the cap is dropped and the list is no longer complete: what it says about "nothing matches" is
        # unknown, and no value is ever compared in a cut-down form
        oversize = False
        for c in cleaned:
            for k in [k for k, v in c.items() if isinstance(v, str) and len(v) > MAX_FIELD_CHARS]:
                del c[k]
                oversize = True
        # page.getBy*() / page.locator() do not enter a frame: its elements answer only a frame locator, which the
        # list cannot follow (a frame chain is "unknown" here, and the live page says what it resolves to)
        self.elements = [e for e in cleaned if not e.get("frame")]
        self.in_frames = [e for e in cleaned if e.get("frame")]
        # an empty list is never authoritative: "nothing is listed" must not read as "this matches nothing"
        self.complete = bool(complete) and bool(cleaned) and not oversize
        aria = aria if isinstance(aria, str) else ""
        self.aria_over = not aria_ok(aria)          # a snapshot over its size caps answers nothing
        # redacted here as well as at intake: refusal hints quote snapshot names back to the model, so an Inventory
        # built from any other path must not carry typed values (redaction is idempotent and linear)
        self.aria = "" if self.aria_over else redact_typed_values(aria)

    def inherit(self, other: "Inventory") -> None:
        """Take the gaps of `other` (a scope's inventory is a view of the page's: it is as incomplete as the page's)."""
        self.copy_more = max(self.copy_more, other.copy_more)
        self.withheld, self.withheld_data = max(self.withheld, other.withheld), max(self.withheld_data, other.withheld_data)

    # ---- css -------------------------------------------------------------------------------
    def _css_count(self, css: str) -> Optional[int]:
        hits = self._css_hits(css, True)
        return None if hits is None else len(hits)

    def _css_hits(self, css: str, need_complete: bool = True) -> Optional[List[Dict[str, Any]]]:
        read = compound_of(css)
        if read is None or (need_complete and not self.complete):
            return None
        tag, conds, scoped = read
        if not scoped:
            return None                         # bare .class: the listing does not cover every element
        hits: List[Dict[str, Any]] = []
        for el in self.elements:
            held = compound_holds(tag, conds, el)
            if held is None:
                return None
            if held:
                hits.append(el)
        return hits

    def _attr_count(self, attr: str, want: str, exact: bool, visible_only: bool = False) -> Optional[int]:
        if not self.complete:
            return None
        return sum(1 for el in self.elements
                   if el.get(attr) and _match_text(want, el[attr], exact) and not (visible_only and el.get("hidden")))

    def _label_count(self, want: str, exact: bool) -> Optional[int]:
        if not self.complete:
            return None
        n = 0
        for el in self.elements:
            if el.get("hidden"):
                continue
            texts = [el.get("label", ""), el.get("aria_label", "")]       # getByLabel also reads aria-label
            if el.get("tag") in ("input", "textarea", "select"):
                texts.append(el.get("name", ""))                # aria-label / value of a form control
            n += any(t and _match_text(want, t, exact) for t in texts)
        return n

    def _text_count(self, want: str, exact: bool) -> Optional[int]:
        """getByText matches: from the aria snapshot when there is one, else from the elements' own text (a complete
        list only: a cut-short one could be missing the element)."""
        if self.aria_over:
            return None
        listed = any(el.get("copy_only") and el.get("text") and _match_text(want, el["text"], exact) for el in self.elements)
        if (not self.aria or listed) and self._listing_short(exact, listed):
            return None
        if not self.aria and _withheld_shape(want):
            return None                                     # the listing never holds such copy: its silence says nothing
        if not self.aria:
            if not self.complete:
                return None
            return sum(1 for el in self.elements if not el.get("hidden") and el.get("text")
                       and _match_text(want, el["text"], exact))
        n = 0
        for line in self.aria.splitlines():
            m = _ROLE_RE.match(role_line(line))
            if m:
                n += any(t and _match_text(want, t, exact) for t in ((m.group(3) or ""), (m.group(4) or "")))
        # the snapshot folds plain text next to other text into one line, so an element listed for its copy is counted
        # from the listing; the larger count is the safe one (the snapshot has the roles, the listing has the plain text)
        if self.complete:
            hits = [el for el in self.elements if not el.get("hidden") and el.get("text") and _match_text(want, el["text"], exact)]
            if any(el.get("copy_only") for el in hits):
                n = max(n, len(_innermost(hits)))
        return n

    def _listing_short(self, exact: bool, listed: bool) -> bool:
        """True when a text count read from the listing could be short: copy was left out for room; or the probe withheld visible
        copy (too long, an address, digits) that could contain a substring text; or withheld copy inside a control or data
        widget (an option, a grid cell: short, so it can equal an exact text); or the match includes a copy_only element, whose
        substring matches the withheld copy could share."""
        return bool(self.copy_more or (listed and not exact) or (self.withheld and not exact) or self.withheld_data)

    # ---- entry -----------------------------------------------------------------------------
    def count(self, expr: str) -> Optional[int]:
        """Matches of a locator expression in the inventory; None when it cannot be told."""
        if too_long(expr, MAX_EXPR_CHARS):
            return None
        try:
            with rx.work_budget():
                return self._count(expr)
        except rx.CannotEvaluate:
            return None

    def _count(self, expr: str) -> Optional[int]:
        sp = LOCATOR_SPLIT_RE.search(expr)
        if not sp:
            return None
        rest = expr[sp.start() + 1:]
        name_m = re.match(r"(\w+)\(", rest)
        end = balanced_end(rest, name_m.end() - 1) if name_m else -1
        if not name_m or end < 0:
            return None
        name, args, tail = name_m.group(1), rest[name_m.end():end - 1], rest[end:]
        if re.sub(r"\.(?:first|last)(?:\(\))?|\.nth\(\s*\d+\s*\)", "", tail).strip():
            base = self._base(name, args, expr)       # filter()/locator() chains: only a zero base is certain
            return 0 if base == 0 else None
        base = self._base(name, args, expr)
        if base is None:
            return None
        n = base
        for t in re.findall(r"\.(first|last|nth\(\s*\d+\s*\))", tail):
            k = re.search(r"\d+", t)
            n = min(n, 1) if k is None else (1 if n > int(k.group()) else 0)
        return n

    # ---- which elements (not just how many) ---------------------------------------------------
    def matched(self, expr: str) -> Optional[List[Dict[str, Any]]]:
        """The listed elements (hidden ones included) that a single locator call matches, for comparing a proposal with
        the page of a PASSING run. None when it cannot be told: a scoped chain (filter / locator / a second getBy), a
        regex or an expression this listing does not cover. `.first` / `.last` / `.nth(n)` narrow the set, counted over
        the visible matches in page order (all of them when none is visible)."""
        if too_long(expr, MAX_EXPR_CHARS):
            return None
        try:
            with rx.work_budget():
                hits = self._matched_base(expr)
        except rx.CannotEvaluate:
            return None
        if not hits:
            return hits
        for narrowing in re.findall(r"\.(first|last|nth\(\s*\d+\s*\))", self._tail_of(expr)):
            pool = [h for h in hits if not h.get("hidden")] or hits
            if narrowing == "first":
                hits = pool[:1]
            elif narrowing == "last":
                hits = pool[-1:]
            else:
                k = int(re.search(r"\d+", narrowing).group())
                hits = pool[k:k + 1]
        return hits

    @staticmethod
    def _tail_of(expr: str) -> str:
        """What follows the first locator call of `expr` (its `.first` / `.nth(n)` members)."""
        sp = LOCATOR_SPLIT_RE.search(expr)
        if not sp:
            return ""
        rest = expr[sp.start() + 1:]
        name_m = re.match(r"(\w+)\(", rest)
        end = balanced_end(rest, name_m.end() - 1) if name_m else -1
        return rest[end:] if end >= 0 else ""

    def _matched_base(self, expr: str) -> Optional[List[Dict[str, Any]]]:
        """matched() before `.first` / `.last` / `.nth(n)` narrow it."""
        sp = LOCATOR_SPLIT_RE.search(expr)
        if not sp:
            return None
        rest = expr[sp.start() + 1:]
        name_m = re.match(r"(\w+)\(", rest)
        end = balanced_end(rest, name_m.end() - 1) if name_m else -1
        if not name_m or end < 0:
            return None
        name, args, tail = name_m.group(1), rest[name_m.end():end - 1], rest[end:]
        if re.sub(r"\.(?:first|last)(?:\(\))?|\.nth\(\s*\d+\s*\)", "", tail).strip():
            return None
        lit, opts, exact = _lit_and_opts(args)
        if lit is None:
            return None
        els = self.elements
        if name in ("locator", "frameLocator", "frame_locator"):
            return None if isinstance(lit, rx.RegexLit) else self._css_hits(lit, False)
        if name in ("getByPlaceholder", "get_by_placeholder"):
            return [e for e in els if e.get("placeholder") and _match_text(lit, e["placeholder"], exact)]
        if name in ("getByTestId", "get_by_test_id"):
            return [e for e in els if e.get("testid") == lit]
        if name in ("getByAltText", "get_by_alt_text"):
            return [e for e in els if e.get("alt") and _match_text(lit, e["alt"], exact)]
        if name in ("getByTitle", "get_by_title"):
            return [e for e in els if e.get("title") and _match_text(lit, e["title"], exact)]
        if name in ("getByLabel", "get_by_label"):
            out = []
            for e in els:
                texts = [e.get("label", ""), e.get("aria_label", "")]
                if e.get("tag") in ("input", "textarea", "select"):
                    texts.append(e.get("name", ""))
                if any(t and _match_text(lit, t, exact) for t in texts):
                    out.append(e)
            return out
        if name in ("getByText", "get_by_text"):
            if _withheld_shape(lit) or self._listing_short(exact, any(e.get("copy_only") and e.get("text") and _match_text(lit, e["text"], exact)
                                              for e in els)):
                return None                                 # the listing is missing some copy: which elements match is unknown
            return _innermost([e for e in els      # not by `label`: getByText finds the <label>, never the control it names
                               if any(e.get(k) and _match_text(lit, e[k], exact) for k in ("text", "name"))])
        if name in ("getByRole", "get_by_role"):
            nm = re.search(r"name\s*[:=]\s*(?:'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\")", opts)
            want = (nm.group(1) if nm and nm.group(1) is not None else nm.group(2)) if nm else None
            nm_exact = bool(re.search(r"exact\s*[:=]\s*(?:true|True)", opts))
            out = []
            for e in els:
                if _role_of(e) != lit.lower():
                    continue
                if want is not None and not any(e.get(k) and _match_text(want, e[k], nm_exact)
                                                for k in ("label", "name", "text", "placeholder", "title")):
                    continue
                out.append(e)
            return out
        return None

    def _base(self, name: str, args: str, expr: str) -> Optional[int]:
        lit, rest, exact = _lit_and_opts(args)
        if name in ("locator", "frameLocator", "frame_locator"):
            return self._css_count(lit) if lit and not isinstance(lit, rx.RegexLit) else None
        if lit is None:
            return None
        if name in ("getByPlaceholder", "get_by_placeholder"):
            return self._attr_count("placeholder", lit, exact)
        if name in ("getByTestId", "get_by_test_id"):
            return None if not self.complete else sum(1 for el in self.elements if el.get("testid") == lit)
        if name in ("getByAltText", "get_by_alt_text"):
            return self._attr_count("alt", lit, exact)
        if name in ("getByTitle", "get_by_title"):
            return self._attr_count("title", lit, exact)
        if name in ("getByLabel", "get_by_label"):
            return self._label_count(lit, exact)
        if name in ("getByText", "get_by_text"):
            return self._text_count(lit, exact)
        if name in ("getByRole", "get_by_role"):
            return aria_count(_role_only(expr), self.aria)
        return None


_IMPLIED_ROLE = {"button": "button", "a": "link", "select": "combobox", "textarea": "textbox"}
_INPUT_ROLE = {"checkbox": "checkbox", "radio": "radio", "button": "button", "submit": "button", "reset": "button",
               "range": "slider", "number": "spinbutton", "search": "searchbox"}


def _role_of(el: Dict[str, Any]) -> str:
    """The ARIA role of a listed element: its explicit role, else the one its tag / input type implies."""
    if el.get("role"):
        return str(el["role"]).lower()
    tag = (el.get("tag") or "").lower()
    if tag == "input":
        return _INPUT_ROLE.get((el.get("type") or "text").lower(), "textbox")
    return _IMPLIED_ROLE.get(tag, "")


def _role_only(expr: str) -> str:
    """The getByRole call alone (no chain), so aria_count sees `getByRole(...)` without narrowing."""
    sp = LOCATOR_SPLIT_RE.search(expr)
    if sp is None:
        return expr
    rest = expr[sp.start() + 1:]
    m = re.match(r"\w+\(", rest)
    if m is None:
        return "page." + rest
    return "page." + rest[: balanced_end(rest, m.end() - 1)]            # a receiver, so it is the chain's first call
