"""
===============================================================================
Element Facts (what a listed element is apart from its copy; the tag a locator ends in)
===============================================================================
The substitute gate compares the element a proposal resolves to with the one the passing run acted on. Two facts it
needs come from the listing alone, with no live page:

    kind     an inventory entry without its copy (name, label, placeholder, title, alt, text, aria-label) and its
             layout (box, hidden, frame): the tag, type, role and the hooks. Two elements of one kind differ only in
             what they say, so a rename keeps the kind
    subject  the tag a CSS locator can only resolve to: the tag of the last compound of its last `locator()` call
             (`div[data-name=x] input[type=checkbox]` always ends in an <input>), whatever the scope before it

Features:
    ✓ element_kind / copy_of: an entry split into what it is and what it says
    ✓ subject_tag: the tag a plain locator chain resolves to, or None when the chain does not say (a getBy* call, an
      xpath, a selector list, a `*` or tagless compound, a chain the parser rejects)

Author: PMAC
===============================================================================
"""
import re
from typing import Any, Dict, Optional

from .locator_steps import Chain, try_parse

COPY_KEYS = ("name", "aria_label", "label", "placeholder", "title", "alt", "text")
_LAYOUT_KEYS = ("box", "hidden", "frame")
_NARROWING = {"first", "last", "nth", "filter"}
_TAG_RE = re.compile(r"([A-Za-z][\w-]*)(?![\w-])")
_ENGINE_PREFIX_RE = re.compile(
    r"^\s*(?:xpath|text|id|data-testid|data-test|data-test-id|role|internal:[\w-]+)\s*=|^\s*//|^\s*\.\./")


def element_kind(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The entry without its copy and its layout."""
    skip = set(COPY_KEYS) | set(_LAYOUT_KEYS)
    return {k: v for k, v in entry.items() if k not in skip and v not in ("", None, False)}


def copy_of(entry: Dict[str, Any]) -> Dict[str, str]:
    """What the entry says: its copy attributes that are set."""
    return {k: str(entry[k]) for k in COPY_KEYS if entry.get(k)}


def _chain_of(expr: str) -> Optional[Chain]:
    return try_parse(expr, "python") or try_parse(expr, "ts")


def _last_compound(css: str) -> Optional[str]:
    """The last compound selector of a CSS selector (after its last combinator), or None for a selector list."""
    depth, quote, start = 0, "", 0
    for i, ch in enumerate(css):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and ch == ",":
            return None
        elif depth == 0 and ch in " \t\r\n>+~":
            start = i + 1
    return css[start:].strip() or None


def subject_tag(expr: str) -> Optional[str]:
    """The tag `expr` can only resolve to, from its last `locator('<css>')` call (`.first`, `.nth()`, `.filter()` only
    narrow what that call picked); None when the expression does not say."""
    chain = _chain_of(expr)
    if chain is None:
        return None
    steps = list(chain.steps)
    while steps and steps[-1].method in _NARROWING:
        steps.pop()
    if not steps or steps[-1].method != "locator" or not steps[-1].args or not isinstance(steps[-1].args[0], str):
        return None
    parts = steps[-1].args[0].split(" >> ")                 # Playwright's own chain: the last part is the subject
    css = parts[-1]
    if css.lstrip().startswith("css="):
        css = css.lstrip()[4:]
    elif _ENGINE_PREFIX_RE.match(css):
        return None
    compound = _last_compound(css)
    m = _TAG_RE.match(compound) if compound else None
    return m.group(1).lower() if m else None
