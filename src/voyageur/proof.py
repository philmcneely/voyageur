"""
===============================================================================
Identity Proof (a pick becomes a heal only with positive proof it is the element the step meant)
===============================================================================
A model's pick, or a fingerprint-less guess, is a candidate until something shows it is the element the broken step acted
on. A passing re-run is not that proof (a click on the wrong button can pass), and "nothing contradicts it" is not either. This
module holds the rules; gates.verify_pick applies them and refuses a pick with none as `no_identity_proof`.

With a fingerprint (what the passing run recorded about the element), at least one of:
    identity   a stable attribute (test id, id, name, data-* hook) unchanged on the picked element (a hook many elements share
               counts only with the same row; a generated id never counts)
    copy       the same copy (name / label / text / placeholder / title / alt, any pairing) and a compatible kind
    place      the same place (overlapping box), or the same offset from the recorded neighbours that are still on the page,
               and a compatible kind; top-most at its box centre when the probe says (`top_at_center` / `hit`), and when it
               does not say, not when the copy differs completely, unless the page language changed (translated copy)
    within     the recorded element was a bare wrapper (a container tag or a custom element with no copy, hook or role of its
               own: nothing to compare, and not the kind of a control) and the pick is a form control or widget inside its box
               with no other listed element in that box (the text of the pick's own label does not count), and it fills the wrapper (80% of its width or height and 10% of its area:
               a big container with one widget left is not that widget's wrapper). Stricter than place: the listing must be
               complete, no copy-holding element left out (an unlisted element could share the box) and the probe must say the
               pick is top-most at its box centre
and no contradiction: a hook attribute present on both with DIFFERENT values while the copy differs too (export-csv vs
export-pdf) is another element, whatever else lines up (a removed button's freed place is taken by its neighbour).

Without a fingerprint, the proof comes from the broken locator's own facts (its last selector, role and name, text arguments):
    hook_stem  a stable value of the picked element (id, name, test id, data-*), or its copy, equals the broken locator's hook
               value, or one has every meaningful word of the other, a word counting when it is the other's stem
               (display-name -> display-name-input, username -> user); export-csv and export-pdf share a word but neither has
               all of the other's, so they prove nothing
    copy       the broken locator's role+name equals the pick's role+name, or at least two meaningful words are shared
               (whole-token stems of 5+ letters), or its whole name is inside the only listed element's copy
    renamed    a CSS-only locator whose old id / class / attribute values are all gone from the page, the pick is the only
               visible element of the locator's tag and its copy equals one of the selector's names
    row        the broken locator filters by row text and the pick sits in a row with that text, with a compatible kind
and the same contradiction rule against the broken locator's hook attribute (data-cy=export-csv against data-cy=export-pdf).

A step whose only broken part was a frame hop (frame_heal.py) keeps its own locator after the hop. With a fingerprint that is a
third kind of proof, available only to such a step:
    selector   the locator the step already had, unchanged, is a single compound selector (`button.primary`, `input[type=email]`, `[data-x=y]`)
               that the picked element satisfies and no other listed element does, on a complete listing with no copy-holding element
               left out and a selector the listing covers (an id, hook, role, placeholder, name or type, never a bare class or tag);
               the element is the recorded kind (tag, type) and carries every class the recording had

Features:
    ✓ prove_from_kept(): the selector proof above (a fingerprint and a selector with at least a class, id or attribute in it)
    ✓ broken_facts(): the last selector's tag / id / classes / attributes, role, name, copy words and row text of a call
      (text after a frame hop only; a call that does not parse has no facts and proves nothing)
    ✓ prove(): (proof, why) when the element is proven, None otherwise; contradiction(): the sentence when it is another one
    ✓ Pure functions over plain data: no model, no network, no page

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .element_facts import COPY_KEYS, _chain_of, _last_compound
from .elements import BOX_TOLERANCE, STABLE_KEYS, Box, box_of, describe_control, identity_match, norm_text, same_place, stable_keys
from .frames import frame_hop
from .hashed_copy import is_hashed
from .inventory import _role_of, compound_holds, compound_of
from .locator_policy import is_generated_id

OFFSET_TOLERANCE = 4.0           # px: a neighbour-consistent position survives sub-pixel layout
MIN_WORD = 3
_GENERIC = {"btn", "button", "input", "field", "id", "test", "testid", "cy", "qa", "el", "elem", "the", "and", "for", "with",
            "from", "your", "this", "that"}
_WORDS = re.compile(r"[A-Za-z]+")
_NUMBERS = re.compile(r"\d+")
_STOP = _GENERIC | {"all", "are", "not", "you", "can", "has", "was", "but", "our", "any", "one", "its", "out", "off", "own"}
MIN_STEM = 5                     # copy stems: whole tokens of this length with this many letters in common
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_PART = re.compile(r"""#([\w-]+)|\.([\w-]+)|\[\s*([\w-]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+)))?\s*\]""")
_TAG = re.compile(r"^([A-Za-z][\w-]*)")
_HOOKS = ("testid", "data_qa", "data_cy", "data_test")
_ATTR_FIELD = {"data-testid": "testid", "data-test": "testid", "data-qa": "data_qa", "data-cy": "data_cy",
               "name": "name_attr", "id": "id"}


def words(value: Any) -> Set[str]:
    """The meaningful lower-case words of an identifier or a sentence (camelCase and punctuation split, generic and very
    short words dropped, digit-only tokens dropped: a number says nothing about which element it is)."""
    if is_hashed(value):                             # a recorded copy hash no page copy matched: it has no words
        return set()
    text = _CAMEL.sub(" ", str(value or ""))
    return {w.lower() for w in _WORDS.findall(text) if len(w) >= MIN_WORD and w.lower() not in _GENERIC}


def numbers(value: Any) -> Set[str]:
    return set(_NUMBERS.findall(str(value or "")))


def _match(w: str, b: str) -> bool:
    """The same word, or one the stem of the other (user / username; both at least four letters)."""
    return w == b or (min(len(w), len(b)) >= 4 and (b.startswith(w) or w.startswith(b)))


def _covers(small: Set[str], big: Set[str]) -> bool:
    return all(any(_match(w, b) for b in big) for w in small)


def _stem_related(a: Any, b: Any) -> bool:
    """Equal, or one has every meaningful word of the other (and at least one), a word counting when it is the other's
    stem."""
    if norm_text(a) and norm_text(a) == norm_text(b):
        return True
    wa, wb = words(a), words(b)
    na, nb = numbers(a), numbers(b)
    if na and nb and not (na & nb):                  # order-1 is not order-2: a number counts only beside a matching word
        return False
    return bool(wa and wb and (_covers(wa, wb) or _covers(wb, wa)))


def _copy_words(value: Any) -> Set[str]:
    """Words that may prove a shared copy: no generic words, stopwords or words under three letters."""
    return {w for w in words(value) if w not in _STOP}


def _same_token(a: str, b: str) -> bool:
    """The same word, or both whole tokens of at least five letters with a five-letter common prefix (customer / customers)."""
    return a == b or (min(len(a), len(b)) >= MIN_STEM and a[:MIN_STEM] == b[:MIN_STEM])


def shared_meaningful_words(old: Any, new: Any) -> int:
    """How many distinct meaningful words of `old` have a counterpart in `new`."""
    wb = _copy_words(new)
    return sum(1 for w in _copy_words(old) if any(_same_token(w, b) for b in wb))


def _contained_uniquely(old: Any, e: Dict[str, Any], items: Sequence[Dict[str, Any]]) -> bool:
    """The narrow case a single shared word is enough: every meaningful word of the step's name (at least one) is in the
    pick's copy (Expiry -> Expiry date) and the pick is the only listed element whose copy has it."""
    ow = _copy_words(old)
    if not ow:
        return False
    def has(x: Dict[str, Any]) -> bool:
        return any(all(any(_same_token(w, b) for b in _copy_words(c)) for w in ow) for c in _copies(x))
    return has(e) and not any(i is not e and has(i) for i in items)


def _top(e: Dict[str, Any]) -> Optional[bool]:
    """Whether the element is the top-most at its box centre, when the probe says (`top_at_center` or `hit`), else None."""
    for k in ("top_at_center", "hit"):
        if e.get(k) is not None:
            return bool(e[k])
    return None


def _kind_ok(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """The same kind of control: the same role when both have one, else the same tag."""
    ra, rb = _role_of(a), _role_of(b)
    if ra and rb:
        return ra == rb
    ta, tb = norm_text(a.get("tag")), norm_text(b.get("tag"))
    return bool(ta and ta == tb)


def _copies(e: Dict[str, Any]) -> List[str]:
    return [norm_text(e[k]) for k in (*COPY_KEYS, "name") if e.get(k)]


def _hooks(e: Dict[str, Any]) -> Dict[str, str]:
    extra = sorted(k for k in e if k.startswith("data_") and k not in _HOOKS)
    return {k: str(e[k]) for k in (*_HOOKS, *extra) if e.get(k)}


# ---- with a fingerprint ----------------------------------------------------------------------------------------------
def _same_copy(fp: Dict[str, Any], e: Dict[str, Any]) -> bool:
    return bool(set(_copies(fp)) & set(_copies(e)))


def _neighbour_offset(e: Dict[str, Any], fp: Dict[str, Any], neighbours: Sequence[Dict[str, Any]],
                      items: List[Dict[str, Any]]) -> bool:
    fb, eb = box_of(fp.get("box")), box_of(e.get("box"))
    if fb is None or eb is None:
        return False
    for n in neighbours:
        nb = box_of(n.get("box"))
        now = next((i for i in items if i is not e and any(i.get(k) and i.get(k) == n.get(k) for k in stable_keys(i, n))
                    and identity_match(i, n, [x for x in items if x is not i])), None)
        mb = box_of(now.get("box")) if now else None
        if nb is None or mb is None:
            continue
        if abs((fb[0] - nb[0]) - (eb[0] - mb[0])) <= OFFSET_TOLERANCE and abs((fb[1] - nb[1]) - (eb[1] - mb[1])) <= OFFSET_TOLERANCE:
            return True
    return False


def prove_with_fingerprint(e: Dict[str, Any], fp: Dict[str, Any], items: List[Dict[str, Any]],
                           neighbours: Sequence[Dict[str, Any]] = (), language_changed: bool = False,
                           complete: bool = False) -> Optional[Tuple[str, str]]:
    """(proof, why) that `e` is the fingerprinted element, or None. `complete` says the listing holds every element (the
    wrapper proof needs it: an unlisted element in the wrapper's box would make "the only one" meaningless)."""
    base = dict(fp)
    base.setdefault("tag", e.get("tag") or "")
    shared = [k for k in stable_keys(e, base) if e.get(k) and e.get(k) == base.get(k)
              and not (k == "id" and is_generated_id(str(e[k])))]
    if shared and identity_match(e, base, [i for i in items if i is not e]):
        return "identity", "its " + ", ".join(k.replace("_", "-") for k in shared)[:80] + " is unchanged"
    if _kind_ok(fp, e):
        if _same_copy(fp, e):
            return "copy", "it says the same as the recorded element"
        mine, theirs = box_of(e.get("box")), box_of(fp.get("box"))
        if mine and theirs and same_place(mine, theirs) and _place_sound(e, fp, language_changed):
            return "place", "it is where the recorded element was"
        if _neighbour_offset(e, fp, neighbours, items) and _place_sound(e, fp, language_changed):
            return "place", "it keeps the recorded element's position among its neighbours"
    elif complete and _bare_wrapper(fp) and _is_widget(e) and _top(e) is True and _only_one_inside(e, fp, items) \
            and _hugged(e, fp):
        return "within", "it is the only listed element inside the box of the recorded wrapper, which had nothing of its own to compare"
    return None


# Tags of a layout element: it holds controls and says nothing about them. Any custom element (a hyphen in its name) is one too.
_WRAPPER_TAGS = frozenset({"div", "span", "section", "article", "aside", "header", "footer", "main", "nav", "form", "fieldset",
                           "label", "p", "li", "ul", "ol", "td", "tr", "table", "tbody"})
_CUSTOM_TAG = re.compile(r"[a-z][a-z0-9._]*(?:-[a-z0-9._]+)+")
_WIDGET_TAGS = frozenset({"input", "select", "textarea", "button"})
_WIDGET_ROLES = frozenset({"button", "checkbox", "radio", "switch", "textbox", "searchbox", "combobox", "spinbutton", "slider",
                           "listbox"})


def _bare_wrapper(fp: Dict[str, Any]) -> bool:
    """The recorded element is a layout wrapper with nothing of its own to compare: a container tag or a custom element, no
    copy, no id / name / hook / data-* value, no role, and a box."""
    tag = norm_text(fp.get("tag"))
    if tag not in _WRAPPER_TAGS and not _CUSTOM_TAG.fullmatch(tag):
        return False
    if fp.get("role") or _copies(fp) or _hooks(fp) or any(fp.get(k) for k in STABLE_KEYS):
        return False
    return box_of(fp.get("box")) is not None


def _is_widget(e: Dict[str, Any]) -> bool:
    """A form control or a widget by role: the things a wrapper is put around. A link or any other role proves less."""
    return norm_text(e.get("tag")) in _WIDGET_TAGS or norm_text(e.get("role")) in _WIDGET_ROLES


def _inside(inner: Box, outer: Box) -> bool:
    t = BOX_TOLERANCE
    return (inner[0] >= outer[0] - t and inner[1] >= outer[1] - t and inner[0] + inner[2] <= outer[0] + outer[2] + t
            and inner[1] + inner[3] <= outer[1] + outer[3] + t)


MIN_FILL_AXIS = 0.8      # the widget spans this much of the wrapper's width or height (a row it is as tall as, a column as wide as)
MIN_FILL_AREA = 0.1      # and covers this much of its area: a wrapper that is mostly something else held other content


def _hugged(e: Dict[str, Any], fp: Dict[str, Any]) -> bool:
    """The widget fills the recorded wrapper: at least MIN_FILL_AXIS of its width or height and MIN_FILL_AREA of its area. A
    form, section or list that merely contains the widget, its other content gone, is neither (the s4 input is 164 x 29.6 in a
    1198 x 29.6 row: full height, 13.7% of the area)."""
    outer, mine = box_of(fp.get("box")), box_of(e.get("box"))
    if outer is None or mine is None:
        return False
    return (max(mine[2] / outer[2], mine[3] / outer[3]) >= MIN_FILL_AXIS
            and mine[2] * mine[3] >= MIN_FILL_AREA * outer[2] * outer[3])


def _own_label_text(i: Dict[str, Any], e: Dict[str, Any]) -> bool:
    """`i` is the text of `e`'s own label: it is listed only for its copy (no hook, role or control made it a candidate), sits in
    the same frame and shadow root, and says what `e`'s label (or aria label) says. A checkbox or switch inside a vendor
    component is listed beside the span that reads "On"; that span is the control's label, not a second element."""
    if not i.get("copy_only") or (i.get("frame") or "") != (e.get("frame") or "") or (i.get("shadow") or "") != (e.get("shadow") or ""):
        return False
    said = {norm_text(e.get(k)) for k in ("label", "aria_label") if e.get(k)}
    return bool(said & {norm_text(i.get(k)) for k in ("text", "name") if i.get(k)})


def _only_one_inside(e: Dict[str, Any], fp: Dict[str, Any], items: List[Dict[str, Any]]) -> bool:
    """`e` lies inside the recorded wrapper's box and no other listed element does (an element larger than the wrapper, such as
    its parent, is not inside it), except the text of `e`'s own label."""
    outer, mine = box_of(fp.get("box")), box_of(e.get("box"))
    if outer is None or mine is None or not _inside(mine, outer):
        return False
    return not any(i is not e and (b := box_of(i.get("box"))) is not None and _inside(b, outer) and not _own_label_text(i, e)
                   for i in items)


def _place_sound(e: Dict[str, Any], fp: Dict[str, Any], language_changed: bool = False) -> bool:
    """Place proof also needs the element on top at its box centre when the probe says; when it does not say, place alone
    is not enough if the copy differs completely (both have copy and not one meaningful word in common)."""
    top = _top(e)
    if top is not None:
        return top
    if language_changed:                                  # translated: the copy is expected to differ completely
        return True
    mine, then = _copies(e), _copies(fp)
    if not mine or not then:
        return True
    return any(shared_meaningful_words(t, m) for t in then for m in mine)


def contradiction(then_hooks: Dict[str, str], then_copy: Sequence[str], e: Dict[str, Any]) -> Optional[str]:
    """The sentence when a hook attribute is on both sides with a different value and the copy differs as well (or the
    old side has no copy to compare: only for a fingerprint-less locator, where `then_copy` is empty and the hooks are the
    only facts), else None."""
    mine = _hooks(e)
    for k, v in then_hooks.items():
        if k in mine and mine[k] != v:
            if then_copy and set(then_copy) & set(_copies(e)):
                continue
            return f"its {k.replace('_', '-')} is {mine[k][:60]!r}, the step's was {v[:60]!r}"
    return None


def fingerprint_contradiction(fp: Dict[str, Any], e: Dict[str, Any]) -> Optional[str]:
    if not _copies(fp):
        return None
    return contradiction(_hooks(fp), _copies(fp), e)


# ---- without a fingerprint -------------------------------------------------------------------------------------------
@dataclass
class BrokenFacts:
    """What the broken locator itself says about its element."""
    tag: str = ""
    role: str = ""
    css_only: bool = False
    values: Dict[str, str] = field(default_factory=dict)       # inventory key -> old value (id, name_attr, hooks)
    classes: List[str] = field(default_factory=list)
    attrs: Dict[str, str] = field(default_factory=dict)        # every other attribute value of the last selector
    names: List[str] = field(default_factory=list)             # role name / label / text / placeholder / alt / title
    row_text: List[str] = field(default_factory=list)          # filter(has_text=...) values
    scope_hooks: List[str] = field(default_factory=list)       # ids / data-* values of the selector's ancestor compounds
    scope_tags: List[str] = field(default_factory=list)        # custom-element tags among them (a shadow host: order-flag)
    scope_unsafe: bool = False      # the scope cannot be read safely: a negation, a constrained own compound, a position

    def known(self) -> bool:
        return bool(self.values or self.classes or self.attrs or self.names or self.row_text or self.scope_hooks
                    or self.scope_tags)


def broken_facts(call: str) -> BrokenFacts:
    """The facts of the broken call's own element: its last step, plus the row text of its filters."""
    hop = frame_hop(call)
    text = ("page" + call[len(hop[0]):]) if hop else call
    chain = _chain_of(text)
    f = BrokenFacts()
    if chain is None:
        return f
    steps = [s for s in chain.steps if s.method not in ("first", "last", "nth")]
    f.css_only = bool(steps) and all(s.method in ("locator", "filter", "nth") for s in steps)
    for s in steps:
        if s.method == "filter":
            for k in ("has_text", "hasText"):
                if isinstance(s.options.get(k), str):
                    f.row_text.append(s.options[k])
    last = next((s for s in reversed(steps) if s.method != "filter"), None)
    if last is None:
        return f
    arg = last.args[0] if last.args and isinstance(last.args[0], str) else ""
    m = last.method.lower().replace("_", "")
    if m == "getbyrole":
        f.role = arg.lower()
        if isinstance(last.options.get("name"), str):
            f.names.append(last.options["name"])
    elif m in ("getbylabel", "getbytext", "getbyplaceholder", "getbyalttext", "getbytitle"):
        f.names.append(arg)
    elif m == "getbytestid":
        f.values["testid"] = arg
    elif m == "locator":
        _read_css(arg.split(" >> ")[-1], f)
        _read_scope(arg, f, last=True)
    for st in steps:                                     # earlier locator() steps are scope too: locator("#row").locator("b")
        if st is not last and st.method == "locator" and st.args and isinstance(st.args[0], str):
            _read_scope(st.args[0], f, last=False)
    if any(st.method in ("first", "last", "nth") for st in chain.steps):
        f.scope_unsafe = True                            # .nth(1) picked one of several: the scope alone is not its identity
    f.scope_hooks = list(dict.fromkeys(f.scope_hooks))[-MAX_SCOPE_HOOKS:]
    f.scope_tags = list(dict.fromkeys(f.scope_tags))[-MAX_SCOPE_HOOKS:]
    return f


_COMBINATOR = re.compile(r"\s*(?:>>|[>+~]|\s)\s*")


def _compounds(css: str) -> List[str]:
    """The compound selectors of `css` at top level (outside quotes, brackets and parentheses), in order."""
    out, depth, quote, start, i, n = [], 0, "", 0, 0, len(css)
    while i < n:
        ch = css[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif depth == 0 and (ch in " \t>+~"):
            if css[start:i].strip():
                out.append(css[start:i].strip())
            start = i + 1
        i += 1
    if css[start:].strip():
        out.append(css[start:].strip())
    return out


_FUNCTIONAL = re.compile(r":(?:not|is|where|has|has-text|text|text-is|nth-\w+|matches|near|left-of|right-of|above|below)\(", re.I)


def _read_scope(css: str, f: BrokenFacts, last: bool) -> None:
    """The scope a CSS argument names: its compounds before the element's own (all of them for an earlier step). Only the
    innermost scope compound's hooks count. Any functional pseudo-class makes the scope unreadable (`:not(#r1)` names what
    the element is NOT in), and so does an own compound that says more than a tag and classes (`button[type=submit]`,
    `textarea#reason`, `button:has-text(...)`: the scope alone cannot stand for those)."""
    if _FUNCTIONAL.search(css):
        f.scope_unsafe = True
        return
    parts = _compounds(css[:MAX_SCOPE_CHARS])
    if last:
        own = parts[-1] if parts else ""
        if re.search(r"[#\[:]", own):
            f.scope_unsafe = True
            return
        parts = parts[:-1]
    if not parts:
        return
    inner = parts[-1]
    tag = _TAG.match(inner)
    if tag and "-" in tag.group(1):
        f.scope_tags.append(tag.group(1).lower())
    hooks = []
    for m in _PART.finditer(inner):
        ident, _cls, attr, _op, v1, v2, v3 = m.groups()
        value = next((v for v in (v1, v2, v3) if v is not None), None)
        if ident:
            hooks.append(ident)
        elif attr and value and (attr == "id" or attr.startswith("data-")):
            hooks.append(value)
    if last or not f.scope_hooks:                        # the step nearest the element names the scope
        f.scope_hooks = hooks or f.scope_hooks


MAX_SCOPE_CHARS = 1000


def _hosts(e: Dict[str, Any]) -> List[str]:
    """The shadow hosts an element sits in, outermost first (the probe writes them joined)."""
    return re.split(r"[\s>/]+", str(e.get("shadow") or ""))


MAX_SCOPE_HOOKS = 8
# container names that say where on the page, not what: a renamed #main or #panel-x proves nothing by "main" or "panel"
_CONTAINER_WORDS = {"main", "app", "root", "content", "container", "wrapper", "page", "body", "layout", "form", "panel",
                    "section", "view", "wrap", "inner", "outer", "box", "block", "area", "region", "frame", "modal", "dialog"}
_HOOK_KEYS = ("id", "testid", "data_qa", "data_cy", "data_test", "row")


def _classes(e: Dict[str, Any]) -> Set[str]:
    """The element's class names, plus the readable name inside each CSS-module class (`_toggle_41f98` -> toggle: a
    build hash beside it, with a digit in it). `btn-danger` adds nothing: no hash part, so `.btn` does not match it."""
    out: Set[str] = set()
    for c in (c.lower() for k in ("class", "class_") for c in str(e.get(k) or "").split()):
        out.add(c)
        parts = [p for p in re.split(r"[_-]+", c) if p]
        if len(parts) >= 2 and any(any(ch.isdigit() for ch in p) for p in parts):
            out |= {p for p in parts if p.isalpha()}
    return out


def _scoped(e: Dict[str, Any], f: BrokenFacts, items: Sequence[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """The pick is the element the scoped selector meant: visible, of the selector's tag, with every class it named, and
    the ONLY such element in the innermost scope it named (its row is that hook, or it is in the named custom element's
    shadow root); or, when that hook is on no listed element, the page's only such element with a value sharing a specific
    (non-container) word of a 2+ word hook (panel-notes -> name=note). Refused for any scope that cannot be read safely."""
    if f.scope_unsafe or not f.tag or not (f.scope_hooks or f.scope_tags) or e.get("frame") or e.get("hidden"):
        return None
    want = {c.lower() for c in f.classes}
    if norm_text(e.get("tag")) != f.tag or not want <= _classes(e):
        return None
    same = [i for i in items if not i.get("hidden") and not i.get("frame") and norm_text(i.get("tag")) == f.tag
            and want <= _classes(i)]
    if not any(i is e for i in same):
        return None
    for h in f.scope_hooks:
        if e.get("row") == h and sum(1 for i in same if i.get("row") == h) == 1:
            return "scoped", f"it is the only {f.tag} in {h!r}, the scope the step named"
    for t in f.scope_tags:
        if t in _hosts(e) and sum(1 for i in same if t in _hosts(i)) == 1:
            return "scoped", f"it is the only {f.tag} inside <{t}>, the component the step named"
    if len(same) != 1:
        return None
    present = {str(i.get(k)) for i in items for k in _HOOK_KEYS if i.get(k)}
    mine = set()
    for k, v in e.items():
        if k in (*STABLE_KEYS, *COPY_KEYS) and isinstance(v, str) and v:
            mine |= words(v)
    for h in f.scope_hooks:
        hw = words(h)
        specific = hw - _CONTAINER_WORDS
        if h in present or len(hw) < 2 or not specific:
            continue
        if any(_match(w, m) for w in specific for m in mine):
            return "scoped", f"the scope {h!r} is gone and it is the page's only {f.tag}, named like it"
    return None


def _gone(f: BrokenFacts, items: Sequence[Dict[str, Any]]) -> bool:
    """Every old id / class / attribute value of the broken selector is absent from the listed elements."""
    for k, v in f.values.items():
        if any(i.get(k) == v for i in items):
            return False
    for c in f.classes:
        if any(c in str(i.get("class") or "").split() for i in items):
            return False
    return True


def _hook_contradiction(f: BrokenFacts, e: Dict[str, Any]) -> Optional[str]:
    mine = _hooks(e)
    for k, v in f.values.items():
        if k in _HOOKS or k.startswith("data_"):
            if k in mine and not _stem_related(v, mine[k]):
                return f"its {k.replace('_', '-')} is {mine[k][:60]!r}, the step's was {v[:60]!r}"
    return None


def _kind_of_broken(f: BrokenFacts, e: Dict[str, Any]) -> bool:
    if f.role and _role_of(e) != f.role:
        return False
    if f.tag and norm_text(e.get("tag")) != f.tag:
        return False
    return True


def _read_css(css: str, f: BrokenFacts) -> None:
    compound = _last_compound(css) or ""
    tag = _TAG.match(compound)
    f.tag = tag.group(1).lower() if tag else ""
    for m in _PART.finditer(compound):
        ident, cls, attr, _op, v1, v2, v3 = m.groups()
        value = next((v for v in (v1, v2, v3) if v is not None), None)
        if ident:
            f.values["id"] = ident
        elif cls:
            f.classes.append(cls)
        elif attr:
            key = _ATTR_FIELD.get(attr) or (attr.replace("-", "_") if attr.startswith("data-") else None)
            if key and value:
                f.values[key] = value
            elif value:
                f.attrs[attr] = value
                if attr in ("aria-label", "title", "placeholder", "alt"):
                    f.names.append(value)


def prove_from_locator(e: Dict[str, Any], f: BrokenFacts, items: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """(proof, why) from the broken locator's facts that `e` is the element it meant, or None."""
    if not f.known() or not _kind_of_broken(f, e):
        return None
    scoped = _scoped(e, f, items)
    if scoped:
        return scoped
    mine = {k: str(e[k]) for k in (*STABLE_KEYS, *_hooks(e)) if e.get(k) and not (k == "id" and is_generated_id(str(e[k])))}
    for old in f.values.values():
        for k, v in mine.items():
            if _stem_related(old, v):
                return "hook_stem", f"its {k.replace('_', '-')} {v[:60]!r} is the step's {old[:60]!r} renamed or extended"
        for c in (e.get(k) for k in COPY_KEYS if e.get(k)):
            # invoice-3 is not proven by copy saying "Invoice": the number told the instances apart, so the copy must carry it
            if numbers(old) and not (numbers(old) & numbers(c)):
                continue
            if _stem_related(old, c):
                return "hook_stem", f"its copy {str(c)[:60]!r} is the step's {old[:60]!r} spelled out"
    exact = bool(f.names) and any(norm_text(n) in _copies(e) for n in f.names)
    if f.role and exact:
        return "copy", f"same role and name ({f.role} {f.names[0][:60]!r})"
    if any(shared_meaningful_words(n, c) >= 2 for n in f.names for c in _copies(e)):
        return "copy", "it shares at least two meaningful words with the copy the step used"
    if any(_contained_uniquely(n, e, items) for n in f.names):
        return "copy", "the step's whole name is inside its copy and no other listed element says it"
    if f.row_text and any(norm_text(t) and norm_text(t) in norm_text(e.get("row")) for t in f.row_text):
        return "row", "it is in the row the step filtered by"
    if f.css_only and f.tag and (f.values or f.classes) and _gone(f, items):
        twins = [i for i in items if not i.get("hidden") and norm_text(i.get("tag")) == f.tag]
        if len(twins) == 1 and twins[0] is e and exact:
            return "renamed", f"the step's selector no longer matches and this is the only visible <{f.tag}> and says the same"
    return None


def _kept_compound(kept: str) -> Optional[Tuple[str, List[Any]]]:
    """(tag, conditions) of the step's own locator when it is one `locator("<compound>")` call naming a class, id or attribute
    on elements the listing holds every one of (compound_of's `scoped`: a bare class or tag could match elements it left out)."""
    chain = _chain_of("page" + kept)
    if chain is None or len(chain.steps) != 1 or chain.steps[0].method != "locator":
        return None
    arg = chain.steps[0].args[0] if chain.steps[0].args else None
    read = compound_of(arg) if isinstance(arg, str) else None
    return (read[0], read[1]) if read and read[1] and read[2] else None


def prove_from_kept(e: Dict[str, Any], fp: Optional[Dict[str, Any]], kept: str, items: List[Dict[str, Any]],
                    complete: bool = False) -> Optional[Tuple[str, str]]:
    """(proof, why) when the locator the step already had (`kept`, the call text after a healed frame hop) still selects `e`
    and no other listed element, and `e` is the recorded kind with the recorded classes; None otherwise. "No other listed
    element" is only worth something when the listing holds every element such a selector can match: a complete listing, no
    copy-holding element left out (`copy_cut`), and a selector on an id, hook, role, placeholder, name or type."""
    if not complete or any(i.get("copy_cut") for i in items):
        return None
    read = _kept_compound(kept) if fp else None
    if read is None or fp is None or not _kind_ok(fp, e) or norm_text(fp.get("tag")) != norm_text(e.get("tag")):
        return None
    if fp.get("type") and e.get("type") and fp["type"] != e["type"]:
        return None
    if not set(str(fp.get("class") or "").split()) <= set(str(e.get("class") or "").split()):
        return None
    tag, conds = read
    if compound_holds(tag, conds, e) is not True:
        return None
    if any(compound_holds(tag, conds, i) is not False for i in items if i is not e and not i.get("hidden")):
        return None
    return "selector", "the step's own selector still selects it, and no other listed element"


def prove(e: Dict[str, Any], fingerprint: Optional[Dict[str, Any]], call: str, items: List[Dict[str, Any]],
          neighbours: Sequence[Dict[str, Any]] = (), language_changed: bool = False,
          kept: str = "", complete: bool = False) -> Tuple[Optional[Tuple[str, str]], Optional[str]]:
    """((proof, why) or None, contradiction sentence or None) for `e` as the element of `call`. `kept` is the part of the call
    after a healed frame hop, when that is what is being healed; `complete` whether the listing holds every element."""
    if fingerprint:
        bad = fingerprint_contradiction(fingerprint, e)
        if bad:
            return None, bad
        return (prove_with_fingerprint(e, fingerprint, items, neighbours, language_changed, complete)
                or (prove_from_kept(e, fingerprint, kept, items, complete) if kept else None)), None
    f = broken_facts(call)
    bad = _hook_contradiction(f, e)
    return (None, bad) if bad else (prove_from_locator(e, f, items), None)


def describe(e: Dict[str, Any]) -> str:
    return describe_control(e)
