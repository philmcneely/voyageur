"""
===============================================================================
Locator Policy (the most stable locator for an element)
===============================================================================
A heal has two parts: finding the element the broken step meant, and writing a locator for it. The model (or a saved
fingerprint) does the first; this module does the second. Given what the page inventory says about the element, it lists
locators in order of how well they survive UI changes, so the service writes the first one that is unique on the page
instead of whatever phrasing a model happened to prefer. Nothing a model says is ever pasted into a locator: every value
below comes from the element's own attributes and is quoted for the target language.

Order (stable attributes first, copy-based locators last):
    testid       data-testid                       get_by_test_id / getByTestId
    id           a hand-written id                 locator("#id")
    name         the name attribute                locator('tag[name="..."]')
    css          one compound selector on a plain  locator("tag.class")          (a fallback: see STABILITY_ORDER)
                 data-* attribute or on classes    locator('tag[data-menu="tag"]')
    role         role + accessible name            get_by_role / getByRole       (copy-based)
    label        the associated label text         get_by_label / getByLabel     (copy-based)
    placeholder  the placeholder text              get_by_placeholder / getByPlaceholder (copy-based)
    text         the element's own visible text    get_by_text / getByText       (copy-based, a last resort)
Structural CSS chains (div > div > span) are never produced.

Features:
    ✓ candidates(): every applicable locator for one element, best first, in the target language and receiver
    ✓ is_generated_id(): skips framework-generated ids (React :r1:, react-select-3-input, mui-123, hashes,
      timestamps) that change between builds
    ✓ facts_of(): ElementFacts from one inventory element; the tag must be a plain lower-case element name
      (^[a-z][a-z0-9-]*$) and class tokens plain CSS identifiers, so no inventory value can break out of a selector
    ✓ stabilize(): the element a locator matches, rewritten as its most stable locator that is unique in the
      inventory (the pick finds the element; this decides how it is written)
    ✓ write(): the ranked locators of one inventory element, each unique on the page and the same element, in both
      languages and in a language-neutral form, with a tier floor (a heal never moves down a tier while a unique locator
      at or above the broken one's tier exists, and the result says when it had to) and every one round-tripped
      through the chain parser and the replacement validator before it is returned
    ✓ render_chain(): a parsed chain written out as code in either client language (None when the language has no spelling
      of it: a regex literal in Python), so a locator the test already holds can be offered in both
    ✓ Text elements (headings, labels, paragraphs, list items, landmarks) are written like any element: a stable attribute first, else
      role + accessible name (a heading with its `level`, from its tag; a landmark an author named, by that name and never its copy;
      never a role for a paragraph, list item or plain label, whose content is no accessible name), else exact text. A copy
      locator is kept only when the request proves it unique; with none left, WriteResult.gap / why say what the request lacked
      (proof_gap: no aria snapshot, no copy counts, an inventory the probe cut, copy held by several elements)
    ✓ parse_order(): a configurable order, validated against the known kinds
    ✓ page_language(): the page's language from <html lang>, else the browser locale, else Content-Language
    ✓ locale_branch(): a copy literal that differs by page language becomes a per-language entry, decoded by a plain
      unquote (no literal_eval or any evaluation of text from a page or a test file)
    ✓ Values are quoted for Python or TypeScript; ids that are not plain CSS identifiers use [id="..."]
    ✓ library_prefixes() / usable_class(): with HEAL_LIBRARY_CLASSES set to a list of component libraries (mui, ant, chakra, mantine,
      mat), a class such as `MuiSelect-select` or `ant-select-item` of a listed library may be written as a css locator
      (`div.MuiSelect-select`); hashed (`css-1x2y3z`, `jss123`, `MuiBox-root-123`), suffixed and state classes (`Mui-focused`,
      `ant-select-open`) never are. Off by default: today's behaviour. Read from the environment here, so a worker process sees it
    ✓ A label is a css candidate by the control it names (`label[for="birthdate"]`, not for a generated id), ahead of its copy
    ✓ A plain data-* attribute (data-menu, data-agent) is a css candidate ahead of classes; state, position and framework
      attributes, flag, numeric, generated and possibly truncated values are never written (_data_attrs)
    ✓ Two forms asked for by name, which the row scope uses and the page-wide default never does (what they say is only
      meaningful inside one container): `role_attr`, an explicit role attribute (`div[role="combobox"]`), and `id_part`, the
      stable tail of a generated id (`[id$="-option-6"]` for `react-select-4-option-6`, see _stable_tail). Both are css
      candidates; kind_of reads an attribute operator (`$=`, `^=`, `*=`, `~=`, `|=`) as css, so they carry that tier

Environment Variables:
    None (the order is passed in by the caller).

Author: PMAC
===============================================================================
"""
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .bounds import too_long
from .element_facts import subject_tag
from .inventory import _NAMED_ROLE, _role_of, heading_level
from .locator_steps import PY, TS, Chain, Regex, chain_to_data, try_parse
from .locators import validate_replacement

TEST_ATTRS = ("data-qa", "data-cy", "data-test")       # test-automation attributes other than data-testid
KINDS = ("testid", "id", "name", "role", "label", "placeholder", "css", "text")
# What a heal tries by default. css and text are not in it: a class selector and visible text are what `write` falls
# back to when nothing above them is unique, so they are asked for by name (see STABILITY_ORDER), never by default.
DEFAULT_ORDER: Tuple[str, ...] = ("testid", "id", "name", "role", "label", "placeholder")
# Every kind, most stable first (the order of RANK below): the order to ask for when a fallback is allowed.
STABILITY_ORDER: Tuple[str, ...] = ("testid", "id", "name", "css", "role", "label", "placeholder", "text")
MAX_CLASSES = 2             # classes combined into one css candidate
MAX_TEXT_CHARS = 200        # element text kept for a text candidate
_TAG = re.compile(r"[a-z][a-z0-9-]*")


@dataclass(frozen=True)
class ElementFacts:
    """What the page inventory reports about one element."""
    tag: str = ""
    test_id: Optional[str] = None
    test_attrs: Tuple[Tuple[str, str], ...] = ()      # data-qa / data-cy / data-test: test ids by another name
    id: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    accessible_name: Optional[str] = None
    label: Optional[str] = None
    placeholder: Optional[str] = None
    classes: Tuple[str, ...] = ()                     # plain, non-generated class names (a css candidate)
    text: Optional[str] = None                        # the element's own visible text (a text candidate)
    data_attrs: Tuple[Tuple[str, str], ...] = ()      # plain data-* attributes that name the element (a css candidate)
    role_attr: Optional[str] = None                   # the explicit role attribute (not the role the tag implies)
    label_for: Optional[str] = None                   # a label's `for`: the id of the control it names (a css candidate)
    level: Optional[int] = None                       # a heading's level (h2 is 2) when the tag says it, else None: written beside role + name


# Shapes framework code generates: React useId (":r1:"), library prefixes with a counter, long digit runs (counters,
# timestamps) and hex hashes. A hand-written id ("pwd2", "billing-email", "h1-title") has none of them.
_GENERATED_ID = re.compile(
    r":"                                                   # React useId, Radix
    r"|^(?:react-select|mui|ember|ext-gen|headlessui|radix|downshift|rc-|el-id)-?\d*"   # library prefixes
    r"|\d{4,}"                                             # counters and timestamps
    r"|(?:^|[-_])[0-9a-f]{8,}(?:$|[-_])"                   # hex hashes as a segment
    r"|^(?=[a-z0-9]{10,}$)(?=.*\d.*\d.*\d)(?=.*[a-z].*[a-z].*[a-z])",  # long mixed letters + digits blob
    re.I)
_CSS_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")   # always fullmatch: `$` would accept a trailing newline

# A plain data-* attribute (data-menu="tag", data-agent="alpha") names an element the way a test id does, when it is not
# one of these: a state or position the page changes as it is used (data-state="open", data-index="3"), a framework's own
# bookkeeping (data-reactid, data-radix-*, Vue's data-v-<hash>), or a value that is a counter, a flag or a generated id.
_DATA_STATE = re.compile(
    r"(?:^|-)(?:state|active|selected|open|opened|expanded|collapsed|checked|disabled|focus|focused|focus-visible|"
    r"hover|hovered|highlighted|pressed|visible|hidden|loading|index|idx|pos|position|order|count|orientation|side|"
    r"align|placement|motion|swipe|time|timestamp|date|uuid|guid|key|nonce)(?:$|-)")
_DATA_FRAMEWORK = re.compile(r"(?:react|reactid|reactroot|radix|headlessui|rbd|rfd|v|vue|svelte|ng|ember|emotion|styled|"
                             r"sentry|gtm|ga|hj|lpignore|1p|bwignore|darkreader|aos|tippy|popper|floating-ui)(?:$|-)")
_DATA_FLAG = frozenset({"true", "false", "on", "off", "yes", "no", "open", "closed", "checked", "unchecked", "active",
                        "inactive", "visible", "hidden", "selected", "enabled", "disabled", "null", "undefined"})
_DATA_NAME = re.compile(r"data-[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_DATA_VALUE_CHARS = 80      # the probe cuts a data-* value at this many UTF-16 units: one that long may be cut short
MAX_DATA_ATTRS = 3          # data-* candidates offered for one element


def _data_attrs(el: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """The element's plain data-* attributes that can name it (inventory key data_<name>, hyphens as underscores)."""
    out = []
    for key, value in el.items():
        if not (isinstance(key, str) and key.startswith("data_") and isinstance(value, str)):
            continue
        attr = "data-" + key[5:].replace("_", "-")
        if attr in ("data-testid", "data-test", "data-qa", "data-cy") or not _DATA_NAME.fullmatch(attr):
            continue                                       # hooks are test ids; a name that is not plain is not written
        rest = attr[5:]
        if _DATA_STATE.search(rest) or _DATA_FRAMEWORK.match(rest):
            continue
        v = value
        if not v or v != v.strip() or len(v.encode("utf-16-le")) >= 2 * _DATA_VALUE_CHARS or any(c.isspace() or _hidden(c) for c in v) \
                or v.lower() in _DATA_FLAG or re.fullmatch(r"-?\d+(?:\.\d+)?", v) or is_generated_id(v) or _brittle(v):
            continue
        out.append((attr, v))
    return tuple(sorted(out))[:MAX_DATA_ATTRS]


_LONG_DIGITS = re.compile(r"\d{6,}")
_HASHY = re.compile(r"[0-9a-f]{8,}", re.I)
_TOKENS = re.compile(r"[-_:.]")


_ID_CHARS = re.compile(r"[A-Za-z0-9_:.-]+")
_ID_CHARS_MAX = 200         # the probe cuts an id at this many characters
_ID_TOKEN = re.compile(r"[^-_]+")
_WORD = re.compile(r"[A-Za-z]{3,}")
_OPAQUE_ROLES = frozenset({"presentation", "none", "generic"})


def _brittle(value: str) -> bool:
    """A value that changes run to run: a long digit run (a timestamp, a sequence) or a hash-like token."""
    return bool(_LONG_DIGITS.search(value) or any(_HASHY.fullmatch(t) and re.search(r"\d", t) and re.search(r"[a-z]", t, re.I)
                                                   for t in _TOKENS.split(value)))


def _stable_tail(value: str) -> Optional[str]:
    """The part of a generated id that does not change between builds, as the suffix to write with `$=`: everything after the
    last unstable token, the delimiter that precedes it included (`react-select-4-option-6` -> `-option-6`). A token is
    unstable when it holds a digit and something follows it (a counter), holds a colon (React's useId) or is a hash. None when
    there is no unstable token to cut at, nothing follows it, the tail has no word of three letters, is itself generated or
    brittle, or the id holds a character outside letters, digits and `_ : . -`, or is as long as the probe keeps (200: it
    may have been cut short, and a suffix of a cut id is not the page's)."""
    if len(value) >= _ID_CHARS_MAX or not _ID_CHARS.fullmatch(value):
        return None
    tokens = list(_ID_TOKEN.finditer(value))
    cut = -1
    for i, m in enumerate(tokens):
        t = m.group()
        if (i < len(tokens) - 1 and re.search(r"\d", t)) or ":" in t or (_HASHY.fullmatch(t) and re.search(r"\d", t)):
            cut = i
    if cut < 0 or cut + 1 >= len(tokens):
        return None
    tail = value[tokens[cut + 1].start() - 1:]
    if not _WORD.search(tail) or is_generated_id(tail) or _brittle(tail):
        return None
    return tail


# ---- component-library class names (opt in) --------------------------------------------------------------------------------
# Libraries whose class names name a component part (`MuiSelect-select`, `ant-select-item`, `mat-menu-item`, `chakra-select`,
# `MantineSelect-input`, `mantine-Select-input`). The prefix is matched case-insensitively at the start of the class and must end
# at a word boundary: a dash or the capital letter of the component name.
LIBRARY_PREFIXES = ("mui", "ant", "chakra", "mantine", "mat")
# CSS-in-JS classes are generated per build whatever the setting (`css-1x2y3z`, `jss123`, `sc-AxjAm`, `emotion-0`): never written.
# emotion's `css-<hash>-<label>` (`css-1xzq8d1-indicatorContainer`) is the same hash with the component's label after it.
_CSS_IN_JS = re.compile(r"^(?:css-(?:[A-Za-z0-9]*\d[A-Za-z0-9]*|[A-Za-z0-9]{5,})(?:-[A-Za-z0-9-]+)?|jss\d+.*|sc-[A-Za-z0-9]{4,}|emotion-\d+.*)$")
# What a listed library also generates or toggles, never written for it: a counter or hash suffix (`MuiBox-root-123`,
# `-a1b2c3d4`), digit runs, and the state a component wears while it is used (`Mui-focused`, `ant-select-open`).
_HASHED_CLASS = re.compile(r"[-_]\d{2,}$|\d{3,}|[-_][0-9a-f]{6,}$|[a-z][-_]?[0-9]+[a-z][0-9]+", re.I)
_CLASS_STATE = re.compile(r"(?:^|-)(?:Mui-[a-z]+|active|focused|focus|open|opened|selected|disabled|checked|expanded|collapsed|hidden|"
                          r"hide|hover|hovered|loading|error|visible|dragging|pressed|invalid|readonly|required|filled)(?:$|-)", re.I)


def library_prefixes(raw: Optional[str] = None) -> Tuple[str, ...]:
    """The component libraries HEAL_LIBRARY_CLASSES lists (`raw`, else the environment), as lower-case names without the dash, in
    the order given. Empty (off) when unset. An unknown name is ignored here (the service refuses it at startup, config.py)."""
    text = os.environ.get("HEAL_LIBRARY_CLASSES", "") if raw is None else raw
    out: List[str] = []
    for part in text.split(","):
        name = part.strip().lower().rstrip("-")
        if name in LIBRARY_PREFIXES and name not in out:
            out.append(name)
    return tuple(out)


def is_library_class(cls: str, prefixes: Tuple[str, ...]) -> bool:
    """True when `cls` starts with a listed library prefix and the prefix ends at a word boundary (a dash, or the capital letter
    of the component name, followed by a lower-case letter): `MuiSelect-select` and `ant-select-item` for mui and ant, not
    `Muiltiple`, `antelope` or the all-caps `MATRIX-row` and `ANTENNA`."""
    low = cls.lower()
    return any(low.startswith(p) and len(cls) > len(p)
               and (cls[len(p)] == "-" or (cls[len(p)].isupper() and cls[len(p) + 1:len(p) + 2].islower())) for p in prefixes)


_NAMED_QUOTED = re.compile(r"""'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)\"""")
_NAMED_CLASS = re.compile(r"""\.([A-Za-z_][A-Za-z0-9_-]*)""")


def _named_in(call: str) -> Tuple[str, ...]:
    """Classes without a digit that the broken call names in a selector string (`.MuiSelect-select`)."""
    out: List[str] = []
    for m in _NAMED_QUOTED.finditer(call[:4000]):
        for c in _NAMED_CLASS.findall(m.group(1) if m.group(1) is not None else m.group(2)):
            if not re.search(r"\d", c) and c not in out:
                out.append(c)
    return tuple(out[:8])


def usable_class(cls: str, prefixes: Tuple[str, ...] = ()) -> bool:
    """True when `cls` may be written as a css class. A CSS-in-JS class never is. A class of a library listed in `prefixes` is,
    unless it is hashed, suffixed with a counter or a state. Any other class is when it is not generated (today's rule)."""
    if not _CSS_IDENT.fullmatch(cls) or _CSS_IN_JS.fullmatch(cls):
        return False
    if is_library_class(cls, prefixes):
        return not (_HASHED_CLASS.search(cls) or _CLASS_STATE.search(cls) or _brittle(cls))
    return not is_generated_id(cls)


def is_generated_id(value: str) -> bool:
    """True for ids a framework generates (they change between builds, so a locator on them breaks)."""
    return bool(_GENERATED_ID.search(value))


def parse_order(raw: str) -> Tuple[str, ...]:
    """A comma-separated order of locator kinds, validated (ValueError names the unknown kind)."""
    order = tuple(k.strip().lower() for k in raw.split(",") if k.strip())
    unknown = [k for k in order if k not in KINDS]
    if unknown:
        raise ValueError(f"unknown locator kind(s) {', '.join(unknown)}; allowed: {', '.join(KINDS)}")
    return order


_INVISIBLE = frozenset({"Cf", "Zl", "Zp", "Cc"})


def _hidden(c: str) -> bool:
    """A character that renders as nothing or reorders text (bidi overrides and isolates, zero width, separators,
    controls): Trojan Source. It is always written as an escape, so a reviewer sees what the literal holds."""
    return unicodedata.category(c) in _INVISIBLE


def _py(value: str) -> str:
    """A double-quoted Python string literal (JSON quoting); every Cf / Zl / Zp / Cc character is a \\uXXXX escape."""
    out = []
    for c in json.dumps(value, ensure_ascii=False):
        n = ord(c)
        out.append((f"\\u{n:04x}" if n <= 0xFFFF else f"\\U{n:08x}") if _hidden(c) else c)
    return "".join(out)


_TS_ESCAPES = {"\\": "\\\\", "'": "\\'", "\n": "\\n", "\r": "\\r"}


def _ts(value: str) -> str:
    """A single-quoted TypeScript string literal; line breaks and every Cf / Zl / Zp / Cc character are escapes
    (\\n for a line feed, \\uXXXX otherwise) so the literal stays on its line and shows what it holds."""
    out = []
    for c in value:
        n = ord(c)
        if c in _TS_ESCAPES:
            out.append(_TS_ESCAPES[c])
        elif _hidden(c):
            out.append(f"\\u{n:04x}" if n <= 0xFFFF else f"\\u{{{n:x}}}")
        else:
            out.append(c)
    return "'" + "".join(out) + "'"


def _attr_value(value: str, py: bool) -> str:
    """A CSS attribute value quoted with the quote the outer literal does not use (Python literals are double-quoted,
    TypeScript ones single-quoted), so the selector needs no escaping and reads back as written; a value holding that
    quote falls back to CSS escaping."""
    q = "'" if py else '"'
    return q + value.replace("\\", "\\\\").replace(q, "\\" + q) + q


def _id_selector(value: str, py: bool = True) -> str:
    return f"#{value}" if _CSS_IDENT.fullmatch(value) else f"[id={_attr_value(value, py)}]"


def candidates(facts: ElementFacts, language: str, receiver: str = "page",
               order: Sequence[str] = DEFAULT_ORDER, named_classes: Tuple[str, ...] = ()) -> List[str]:
    """Every locator `facts` supports, best first per `order`, written for `language` on `receiver`. The caller keeps
    the first one that matches exactly one element on the live page. `named_classes` are classes the broken call itself
    names (see stages.named_classes): they still pass usable_class, and (next to the element's explicit role attribute) are written as
    `tag[role=r].class`, which the inventory can count where a bare `tag.class` it cannot."""
    py = language == "python"
    q = _py if py else _ts
    tag = facts.tag if _TAG.fullmatch(facts.tag or "") else ""       # a tag is only ever written if it is a plain name
    out: List[str] = []
    for kind in order:
        if kind == "testid":
            if facts.test_id:
                out.append(f"{receiver}.{'get_by_test_id' if py else 'getByTestId'}({q(facts.test_id)})")
            for attr, value in facts.test_attrs:
                out.append(f"{receiver}.locator({q(f'[{attr}={_attr_value(value, py)}]')})")
        elif kind == "id" and facts.id and not is_generated_id(facts.id):
            out.append(f"{receiver}.locator({q(_id_selector(facts.id, py))})")
        elif kind == "name" and facts.name:
            selector = f"{tag}[name={_attr_value(facts.name, py)}]"
            out.append(f"{receiver}.locator({q(selector)})")
        elif kind == "css":
            for attr, value in facts.data_attrs:
                out.append(f"{receiver}.locator({q(f'{tag}[{attr}={_attr_value(value, py)}]')})")
            if facts.label_for and tag == "label" and not is_generated_id(facts.label_for):
                out.append(f"{receiver}.locator({q(f'label[for={_attr_value(facts.label_for, py)}]')})")
            lib = library_prefixes()
            classes = [c for c in facts.classes if usable_class(c, lib)][:MAX_CLASSES]
            if classes:
                out.append(f"{receiver}.locator({q(tag + ''.join('.' + c for c in classes))})")
            # A class on a tag the probe lists only when it carries a hook cannot be counted from the listing, but a class
            # next to an explicit role attribute can (the probe lists every element with a role): written only for a class
            # the broken call named, which is the only case the caller passes `named_classes` for
            named = [c for c in classes if lib and c in named_classes and is_library_class(c, lib)]     # only when opted in
            if named and facts.role and _CSS_IDENT.fullmatch(facts.role):
                out.append(f"{receiver}.locator({q(tag + '[role=' + _attr_value(facts.role, py) + ']' + '.' + named[0])})")
        elif kind == "role_attr" and tag and facts.role_attr and facts.role_attr.lower() not in _OPAQUE_ROLES:
            out.append(f"{receiver}.locator({q(tag + '[role=' + _attr_value(facts.role_attr, py) + ']')})")
        elif kind == "id_part" and facts.id and is_generated_id(facts.id) and (tail := _stable_tail(facts.id)):
            out.append(f"{receiver}.locator({q(f'[id$={_attr_value(tail, py)}]')})")
        elif kind == "text" and facts.text:
            out.append(f"{receiver}.get_by_text({q(facts.text)}, exact=True)" if py
                       else f"{receiver}.getByText({q(facts.text)}, {{ exact: true }})")
        elif kind == "role" and facts.role and facts.accessible_name and facts.role.lower() not in _OPAQUE_ROLES:
            # a heading's level is part of what it is: written when the tag says it, so `h2 Profil` is not `h3 Profil`
            level = facts.level if facts.role.lower() == "heading" else None
            if py:
                out.append(f"{receiver}.get_by_role({q(facts.role)}, name={q(facts.accessible_name)}"
                           f"{f', level={level}' if level else ''}, exact=True)")
            else:
                out.append(f"{receiver}.getByRole({q(facts.role)}, {{ name: {q(facts.accessible_name)}"
                           f"{f', level: {level}' if level else ''}, exact: true }})")
        elif kind in ("label", "placeholder"):
            value = facts.label if kind == "label" else facts.placeholder
            if value:
                method = {"label": ("get_by_label", "getByLabel"), "placeholder": ("get_by_placeholder", "getByPlaceholder")}
                m = method[kind][0 if py else 1]
                out.append(f"{receiver}.{m}({q(value)}, exact=True)" if py
                           else f"{receiver}.{m}({q(value)}, {{ exact: true }})")
    return out


# How stable each kind of locator is, best first. "css" is one compound selector on a non-copy attribute (input.new-todo,
# [type=email]); "text" is get_by_text; "chain" is anything structural or unknown (div > div > span, scoped chains).
RANK = {"testid": 0, "id": 1, "name": 2, "css": 3, "role": 4, "label": 5, "placeholder": 6, "text": 7, "chain": 8}
_CALL = re.compile(r"\.(get_by_test_id|getByTestId|get_by_role|getByRole|get_by_label|getByLabel|get_by_placeholder|"
                   r"getByPlaceholder|get_by_text|getByText|locator)\(\s*(?:'((?:\\.|[^'\\])*)'|\"((?:\\.|[^\"\\])*)\")")
_KIND_OF_METHOD = {"get_by_test_id": "testid", "getByTestId": "testid", "get_by_role": "role", "getByRole": "role",
                   "get_by_label": "label", "getByLabel": "label", "get_by_placeholder": "placeholder",
                   "getByPlaceholder": "placeholder", "get_by_text": "text", "getByText": "text"}


def kind_of(expr: str) -> str:
    """The RANK kind of a single-call locator expression; "chain" for a scoped chain or anything not recognised."""
    if too_long(expr):
        return "chain"                                    # not read: a locator that long makes no claim of stability
    calls = _CALL.findall(expr)
    if len(calls) != 1:
        return "chain"
    method, single, double = calls[0]
    arg = single or double
    if method != "locator":
        return _KIND_OF_METHOD[method]
    sel = arg.strip()
    if re.fullmatch(r"\[(?:data-testid|data-test-id|data-test|data-qa|data-cy)=[^\]]+\]", sel):
        return "testid"
    if re.fullmatch(r"#[A-Za-z_][\w-]*|\[id=.+\]", sel):
        return "id"
    if re.fullmatch(r"(?:[a-z][a-z0-9-]*)?\[name=.+\]", sel):
        return "name"
    if re.fullmatch(r"(?:[a-z][a-z0-9-]*)?(?:\.[\w-]+|\[[\w-]+(?:[~|^$*]?=[^\]]+)?\])+|[a-z][a-z0-9-]*", sel):
        return "css"
    return "chain"


_LANG = re.compile(r"[a-z]{2,3}")   # a primary language subtag; it becomes a keyword / object key in the test file


def page_language(state: Optional[Dict[str, Any]]) -> str:
    """The primary language subtag of a recorded page ("fr" for lang="fr-CA"): the page's own <html lang>, else the
    browser locale it ran with, else the Content-Language the server sent (its first entry). "" when none was
    recorded. The words on the page never decide it."""
    state = state if isinstance(state, dict) else {}
    for key in ("lang", "browser_lang", "content_language"):
        value = state.get(key)
        raw = value.strip().lower() if isinstance(value, str) else ""
        sub = re.split(r"[-_]", re.split(r"[,;\s]", raw)[0])[0] if raw else ""
        if sub:
            return sub if _LANG.fullmatch(sub) else ""      # the page is untrusted: anything else is no language at all
    return ""


def facts_of(el: Dict[str, Any]) -> ElementFacts:
    """ElementFacts from one inventory element. Values that are not strings (a malformed request) are dropped; a tag
    that is not a plain lower-case element name is dropped too, because it is the one value written into a selector
    without quoting."""
    def s(key: str) -> Optional[str]:
        v = el.get(key)
        return v if isinstance(v, str) and v else None
    attrs = tuple((a, el[k]) for a in TEST_ATTRS for k in (a, a.replace("-", "_")) if isinstance(el.get(k), str) and el[k])
    tag = s("tag") or ""
    classes = tuple(c for c in (s("class") or "").split() if _CSS_IDENT.fullmatch(c))
    text = s("text")
    return ElementFacts(tag=tag if _TAG.fullmatch(tag) else "", test_id=s("testid"), test_attrs=attrs, id=s("id"),
                        name=s("name_attr"), role=s("role"), accessible_name=s("name"), label=s("label"),
                        placeholder=s("placeholder"), classes=classes, text=text[:MAX_TEXT_CHARS] if text else None,
                        data_attrs=_data_attrs(el), role_attr=s("role") if _CSS_IDENT.fullmatch(s("role") or "") else None,
                        label_for=s("for_attr"))


def stabilize(expr: str, inventory: Any, language: str, receiver: str, order: Sequence[str] = DEFAULT_ORDER) -> str:
    """The most stable locator for the one element `expr` matches in `inventory` (inventory.Inventory: matched / count).
    `expr` is kept when that cannot be told (no unique match, no candidate that is itself unique) and when it is already
    as stable as the best candidate (only a strictly better kind replaces it)."""
    if too_long(expr):
        return expr
    hits = inventory.matched(expr)
    if not hits or len(hits) != 1:
        return expr
    mine = RANK[kind_of(expr)]
    if kind_of(expr) == "text" and not _is_control(hits[0]):
        return expr         # a text locator finds the smallest element with the text; a listed container only contains it
    for cand in candidates(facts_of(hits[0]), language, receiver, order):
        if RANK[kind_of(cand)] >= mine:
            return expr
        if same_element(inventory, cand, hits[0]):
            return cand
    return expr


_CONTROL_TAGS = frozenset({"input", "select", "textarea", "button", "a", "label", "summary", "option"})


def _is_control(element: Dict[str, Any]) -> bool:
    """A control a person acts on: its listed name is its own, not the text of whatever it contains."""
    return str(element.get("tag") or "").lower() in _CONTROL_TAGS or bool(element.get("role"))


def same_element(inventory: Any, cand: str, element: Dict[str, Any]) -> bool:
    """True when `cand` matches exactly one listed element and it is `element` (a count of 1 alone could be another
    element whose attribute reads back the same)."""
    if inventory.count(cand) != 1:
        return False
    got = inventory.matched(cand)
    return got is None or (len(got) == 1 and got[0] is element)


# ---- copy that differs by page language --------------------------------------------------------------------------
COPY_KINDS = frozenset({"role", "label", "placeholder", "text"})
_LIT = re.compile(r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*\"""")
_PY_LOCALIZED = re.compile(r"""localized\(\s*([\w.]+)\s*,\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')((?:\s*,\s*[a-z]{2,3}\s*=\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'))*)\s*\)""")
_TS_LOCALIZED = re.compile(r"""await localized\(\s*([\w.]+)\s*,\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")\s*,\s*\{([^{}]*)\}\s*\)""")
_ENTRY = re.compile(r"""([a-z]{2,3})\s*[=:]\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")""")

_SIMPLE_ESCAPES = {"\\": "\\", "'": "'", '"': '"', "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
                   "v": "\v"}
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_OCTAL_DIGITS = frozenset("01234567")


def _unquote(lit: str) -> str:
    """The value of a '...' or "..." literal, decoded by a plain scanner with Python's escape rules (so a TypeScript
    escape Python lacks, like \\u{...}, does not decode). Nothing is evaluated. ValueError when it is not one quoted
    literal, holds a raw line break, has a bad escape, or decodes to a lone surrogate (it could not be written as UTF-8)."""
    if len(lit) < 2 or lit[0] not in "'\"" or lit[-1] != lit[0]:
        raise ValueError("not a string literal")
    body, out, i = lit[1:-1], [], 0
    while i < len(body):
        c = body[i]
        if c in "\r\n" or c == lit[0]:
            raise ValueError("undecodable string literal")          # a raw line break, or an unescaped closing quote
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(body):
            raise ValueError("undecodable string literal")
        e = body[i]
        if e in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[e])
            i += 1
        elif e == "\n":
            i += 1                                                  # a backslash-newline continues the line
        elif e in _OCTAL_DIGITS:
            j = i
            while j < len(body) and j < i + 3 and body[j] in _OCTAL_DIGITS:
                j += 1
            out.append(chr(int(body[i:j], 8)))
            i = j
        elif e in "xuU":
            n = {"x": 2, "u": 4, "U": 8}[e]
            digits = body[i + 1:i + 1 + n]
            if len(digits) != n or not all(d in _HEX_DIGITS for d in digits) or int(digits, 16) > 0x10FFFF:
                raise ValueError("undecodable string literal")
            out.append(chr(int(digits, 16)))
            i += 1 + n
        elif e == "N":
            end = body.find("}", i)
            if body[i + 1:i + 2] != "{" or end < 0:
                raise ValueError("undecodable string literal")
            try:
                out.append(unicodedata.lookup(body[i + 2:end]))
            except KeyError:
                raise ValueError("undecodable string literal") from None
            i = end + 1
        else:
            out.append("\\" + e)                                    # an unknown escape keeps its backslash, as in Python
            i += 1
    value = "".join(out)
    if any("\ud800" <= ch <= "\udfff" for ch in value):
        raise ValueError("lone surrogate")
    return value


def _entries(raw: str) -> Dict[str, str]:
    return {k: _unquote(v) for k, v in _ENTRY.findall(raw)}


def _localized_call(receiver: str, default: str, entries: Dict[str, str], language: str) -> str:
    if language == "python":
        return f"localized({receiver}, {_py(default)}, " + ", ".join(f"{k}={_py(v)}" for k, v in entries.items()) + ")"
    body = ", ".join(f"{k}: {_ts(v)}" for k, v in entries.items())
    return f"await localized({receiver}, {_ts(default)}, {{ {body} }})"


def locale_branch(old: str, new: str, lang: str, language: str) -> Optional[str]:
    """`old` with the one copy literal that differs in `new` turned into a per-language entry for `lang`: the original copy
    stays the default and other languages' entries are kept. None when anything but that one literal changed, or when
    `lang` is not a plain language subtag (it is written into the test as a keyword / object key)."""
    if not _LANG.fullmatch(lang or "") or too_long(old) or too_long(new):
        return None
    try:
        return _locale_branch(old, new, lang, language)
    except ValueError:                                   # a literal that does not decode: leave the answer as it is
        return None


def _locale_branch(old: str, new: str, lang: str, language: str) -> Optional[str]:
    receiver = re.split(r"\.(?:get_by_|getBy|locator\(|frame_locator\(|frameLocator\()", old, maxsplit=1)[0]
    loc_re = _PY_LOCALIZED if language == "python" else _TS_LOCALIZED
    slots = list(re.finditer(f"(?:{loc_re.pattern})|{_LIT.pattern}", old))
    if _LIT.sub("§", new) != "".join(
            ("§" if i % 2 else part) for i, part in enumerate(_split_keep(old, slots))):
        return None
    new_lits = _LIT.findall(new)
    if len(new_lits) != len(slots):
        return None
    out, pos, changed = [], 0, False
    for m, lit_new in zip(slots, new_lits):
        out.append(old[pos:m.start()])
        value = _unquote(lit_new)
        loc = loc_re.fullmatch(m.group(0))
        if loc:                                              # already branched: add or replace this language's entry
            entries = _entries(loc.group(3))
            default = _unquote(loc.group(2))
            if value != entries.get(lang, default):
                entries[lang] = value
                changed = True
            out.append(_localized_call(loc.group(1), default, entries, language))
        elif _unquote(m.group(0)) != value:
            if not _COPY_SLOT.search(old[:m.start()]):          # a role, a selector or a test id changed: not copy
                return None
            out.append(_localized_call(receiver, _unquote(m.group(0)), {lang: value}, language))
            changed = True
        else:
            out.append(m.group(0))
        pos = m.end()
    out.append(old[pos:])
    return "".join(out) if changed else None


def _split_keep(text: str, matches: List["re.Match[str]"]) -> List[str]:
    """`text` cut at `matches`: [before, match, between, match, ..., after]."""
    parts, pos = [], 0
    for m in matches:
        parts += [text[pos:m.start()], m.group(0)]
        pos = m.end()
    return parts + [text[pos:]]


# A literal in a copy position: the first argument of a label / placeholder / text locator, or a name option.
_COPY_SLOT = re.compile(r"(?:\.(?:get_by_label|getByLabel|get_by_placeholder|getByPlaceholder|get_by_text|getByText)\(\s*"
                        r"|\bname\s*[=:]\s*)$")
_COPY_CALL = re.compile(r"\.(?:get_by_label|getByLabel|get_by_placeholder|getByPlaceholder|get_by_text|getByText)\(|"
                        r"\.(?:get_by_role|getByRole)\([^)]*\bname\s*[=:]")


def copy_based(expr: str) -> bool:
    """True when any call in the locator depends on the copy: a label, placeholder or text locator, or a role with a
    name (a bare role like get_by_role("dialog") does not)."""
    if too_long(expr):
        return True                                       # not read: assume it depends on the copy (the careful answer)
    return bool(_COPY_CALL.search(expr))


# ---- render: a parsed chain as code ------------------------------------------------------------------------------
_TO_PY = dict(zip(TS.methods, PY.methods))                 # the two tables are built in the same order
_TO_TS = dict(zip(PY.methods, TS.methods))
_OPT_TO_PY = dict(zip(TS.options, PY.options))
_OPT_TO_TS = dict(zip(PY.options, TS.options))
_POSITIONS = ("first", "last")


def _value_code(value: Any, py: bool) -> Optional[str]:
    if isinstance(value, bool):
        return ("True" if value else "False") if py else ("true" if value else "false")
    if isinstance(value, str):
        return _py(value) if py else _ts(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Regex):
        return None if py else f"/{value.source}/{value.flags}"
    if isinstance(value, Chain):
        return render_chain(value, "python" if py else "typescript", "page")
    return None


def render_chain(chain: Chain, language: str, receiver: str = "page") -> Optional[str]:
    """`chain` as code in `language` on `receiver`, whichever language it was parsed from; None when that language has no
    spelling of it (a TypeScript regex literal in Python). Strings are quoted by the same functions the writer uses."""
    py = language == "python"
    names, opts = (_TO_PY, _OPT_TO_PY) if py else (_TO_TS, _OPT_TO_TS)
    out = receiver
    for step in chain.steps:
        method = names.get(step.method, step.method)
        if method in _POSITIONS:
            out += f".{method}" if py else f".{method}()"
            continue
        args = [_value_code(a, py) for a in step.args]
        named = [(opts.get(k, k), _value_code(v, py)) for k, v in step.options.items()]
        if None in args or any(v is None for _, v in named):
            return None
        if py:
            args += [f"{k}={v}" for k, v in named]
        elif named:
            args.append("{ " + ", ".join(f"{k}: {v}" for k, v in named) + " }")
        out += f".{method}({', '.join(args)})"
    return out


# ---- write: the ranked, unique, validated locators of one element ---------------------------------------------------
# The attributes a test id can be named by, and the inventory key each one is listed under. Only these can be proved
# unique from the inventory, so only these can be the project's test id attribute.
_HOOK_KEYS = {"data-testid": "testid", "data-test": "data_test", "data-qa": "data_qa", "data-cy": "data_cy"}
_FORM_TAGS = frozenset({"input", "textarea", "select"})


@dataclass(frozen=True)
class Written:
    """One locator written for an element: its kind and tier, the code in each client language, the language-neutral
    chain (locator_steps.chain_to_data), whether it depends on page copy and, for a row-scoped one, the row's selector."""
    kind: str
    tier: int
    code: Dict[str, str]
    neutral: List[Dict[str, Any]] = field(default_factory=list, hash=False)
    copy_based: bool = False
    scope: str = ""


@dataclass
class WriteResult:
    """The candidates best first; `below_floor` says every one of them sits under the broken locator's tier (nothing at
    or above it was unique); `reason` is "no_stable_locator" when there are none, and `gap` / `why` say what the request lacked
    to prove a copy-based locator unique (or that the element has nothing to name it by)."""
    candidates: List[Written]
    below_floor: bool = False
    floor_kind: str = "chain"
    reason: str = ""
    gap: str = ""             # with no candidate: the first thing the request lacked to prove a copy locator (PROOF_GAPS), or "nothing_stable"
    why: str = ""             # a sentence naming everything it lacked; never page copy


def _swap_receiver(receiver: str, language: str) -> str:
    """The same receiver spelled for `language` (`self.page` in Python is `this.page` in TypeScript)."""
    if language == "python" and receiver.startswith("this."):
        return "self." + receiver[5:]
    if language != "python" and receiver.startswith("self."):
        return "this." + receiver[5:]
    return receiver


def write_facts(el: Dict[str, Any], test_id_attribute: str) -> ElementFacts:
    """ElementFacts for writing: the project's test id attribute is the test id (get_by_test_id), every other hook the
    element carries is a css attribute locator; the role may be the one the tag implies and a form control's accessible
    name falls back to its label."""
    base = facts_of(el)
    hooks = {a: el[k] for a, k in _HOOK_KEYS.items() if isinstance(el.get(k), str) and el[k]}
    role = base.role or _role_of(el) or None
    name = base.accessible_name or (base.label if base.tag in _FORM_TAGS else None)
    if role and not base.role and base.tag in _NAMED_ROLE:
        # a landmark or region the tag implies is named by its author (aria-label, aria-labelledby), never by the copy inside it
        given = el.get("aria_label") or el.get("label")
        name = given if isinstance(given, str) and given else None
    level = heading_level(el) if role == "heading" else None
    return replace(base, test_id=hooks.get(test_id_attribute) if test_id_attribute in _HOOK_KEYS else None,
                   test_attrs=tuple((a, v) for a, v in hooks.items() if a != test_id_attribute), role=role,
                   accessible_name=name, level=level)


def _hook_unique(cand: str, facts: ElementFacts, test_id_attribute: str, inventory: Any, item: Dict[str, Any]) -> bool:
    """A test-id style locator is proved unique here, from the key the attribute is listed under (the inventory's own
    oracle reads data-testid only): exactly one listed element holds that value and it is `item`."""
    # read from the call's shape, never from text anywhere in it: a hook VALUE can spell "get_by_test_id" or "[data-qa="
    if re.match(r"[\w.]+\.(?:get_by_test_id|getByTestId)\(", cand):
        attr, value = test_id_attribute, facts.test_id
    else:
        attr, value = next(((a, v) for a, v in facts.test_attrs
                            if re.match(r"[\w.]+\.locator\(['\"]\[" + re.escape(a) + "=", cand)), (None, None))
    key = _HOOK_KEYS.get(attr or "")
    if not key or not value or not inventory.complete:
        return False
    holders = [e for e in inventory.elements if e.get(key) == value]
    return len(holders) == 1 and holders[0] is item


# What the request lacked when a copy-based locator could not be shown unique: the code (WriteResult.gap) and the sentence
# (WriteResult.why). A sentence names no page copy.
PROOF_GAPS = {
    "aria_snapshot_missing": "its role and name could be shown unique only with page.aria_snapshot, which the request does not carry",
    "aria_snapshot_unusable": "page.aria_snapshot is over its size limits, so its role and name could not be counted",
    "copy_counts_missing": "its text could be shown unique only with the probe's copy counts (inventory_copy_withheld and "
                           "inventory_copy_withheld_data), which the request does not carry",
    "listing_incomplete": "the inventory does not hold all the page's copy (it is cut, or the probe left copy out), so its text "
                          "cannot be shown unique",
    "copy_maybe_cut": "its copy is as long as the probe lists, so it may be the start of longer copy, and an exact match on it is shown "
                      "only by page.aria_snapshot, which holds the whole copy",
    "copy_not_unique": "its copy is held by more than one element and nothing stable tells them apart",
    "not_matched": "what the request carries does not show the page holding this copy under that role or text",
    "unprovable": "its uniqueness cannot be evaluated from the inventory and snapshot the request carries",
    "nothing_stable": "the element has no stable attribute and no copy to name it by",
}


def proof_gap(inventory: Any, kind: str, cand_page: str) -> Tuple[str, str]:
    """(code, sentence) for why copy-based candidate `cand_page` (a Python spelling, as the oracle reads it) was not shown to be
    the one element: the request lacks what proves it, or it is not unique. Asked only after the candidate failed."""
    n = inventory.count(cand_page)
    if n is not None and n > 1:
        return "copy_not_unique", f"its copy is held by {n} elements and nothing stable tells them apart"
    if n is not None:
        return ("copy_not_unique", PROOF_GAPS["copy_not_unique"]) if n == 1 else ("not_matched", PROOF_GAPS["not_matched"])
    if kind == "role":
        code = ("aria_snapshot_unusable" if inventory.aria_over else "aria_snapshot_missing" if not inventory.aria
                else "unprovable")
    elif inventory.cut_copy(cand_page):
        code = "copy_maybe_cut"
    elif not inventory.complete:
        code = "listing_incomplete"
    elif inventory.counts_missing:
        code = "copy_counts_missing"
    elif inventory.copy_more or inventory.withheld or inventory.withheld_data:
        code = "listing_incomplete"
    else:
        code = "unprovable"
    return code, PROOF_GAPS[code]


def _gap_text(gaps: List[Tuple[str, str]]) -> Tuple[str, str]:
    """The (first code, one sentence) of the gaps found for an element's copy locators, each reason once, best locator first."""
    if not gaps:
        return "nothing_stable", PROOF_GAPS["nothing_stable"]
    seen: Dict[str, str] = {}
    for code, sentence in gaps:
        seen.setdefault(code, sentence)
    return gaps[0][0], "no stable attribute names the element, and a copy locator could not be used: " + "; ".join(seen.values())


def write(item: Dict[str, Any], inventory: Any, language: str, receiver: str, broken_kind: Optional[str],
          test_id_attribute: str = "data-testid", order: Sequence[str] = STABILITY_ORDER,
          broken_call: Optional[str] = None, named_classes: Tuple[str, ...] = ()) -> WriteResult:
    """The locators for `item` (an element of `inventory`), best first.

    A locator is kept only if it is unique on the page and is `item` (the inventory is the oracle, or the hook check
    above), passes the chain parser in both languages and the replacement validator for the client's language. The
    floor is the tier of the broken locator (`broken_kind`, a RANK key): candidates at or above it are returned when
    there are any; otherwise the lower ones, with below_floor set. A structural or positional locator is never
    written, so an element with nothing stable and unique gets no candidate."""
    floor = RANK.get(broken_kind or "chain", RANK["chain"])
    facts = write_facts(item, test_id_attribute)
    other = "typescript" if language == "python" else "python"
    if not named_classes and broken_call and library_prefixes():
        named_classes = _named_in(broken_call)                 # a class the broken call itself names (page level too)
    mine = candidates(facts, language, receiver, order, named_classes)
    theirs = candidates(facts, other, _swap_receiver(receiver, other), order, named_classes)
    oracle_src = candidates(facts, "python", "page", order, named_classes)    # the oracle reads Python literals: one spelling
    stub = f"{receiver}.locator('x')"
    kept: List[Written] = []
    gaps: List[Tuple[str, str]] = []           # why each copy-based candidate was not shown unique (said when nothing is written)
    for cand, cand_other, cand_page in zip(mine, theirs, oracle_src):
        kind = kind_of(cand_page)
        if kind == "chain":
            continue
        if kind == "testid":
            unique = _hook_unique(cand, facts, test_id_attribute, inventory, item)
        else:
            unique = same_element(inventory, cand_page, item)
        subject = subject_tag(cand_page)       # a css locator can only resolve to the tag its last selector ends in
        if subject is not None and subject != (item.get("tag") or "").lower():
            continue
        if not unique and kind in COPY_KINDS:
            gaps.append(proof_gap(inventory, kind, cand_page))
        chain = try_parse(cand.replace(receiver, "page", 1), language)
        if not unique or chain is None or try_parse(cand_other.replace(_swap_receiver(receiver, other), "page", 1),
                                                    other) is None:
            continue
        if validate_replacement(broken_call or stub, cand, language) is not None:
            continue
        kept.append(Written(kind, RANK[kind], {language: cand, other: cand_other}, chain_to_data(chain),
                            kind in COPY_KINDS))
    if not kept:
        gap, why = _gap_text(gaps)
        return WriteResult([], False, broken_kind or "chain", "no_stable_locator", gap, why)
    above = [w for w in kept if w.tier <= floor]
    return WriteResult(above or kept, not above, broken_kind or "chain")
