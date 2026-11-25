"""
===============================================================================
Text Filter (a shared stable locator narrowed by a text the broken call named)
===============================================================================
Two subscription rows both carry data-testid=subscription-row; the broken step climbed from the text "My subscription"
to its row by a class that is gone. Nothing of the row's own is unique on the page and it sits in no hooked row, so the
writer has nothing to offer, although the test says which row it means: the one holding "My subscription". This module
writes that: page.get_by_test_id("subscription-row").filter(has_text="My subscription").

The text is never the model's and never a guess at the page's copy: it is one the broken call itself passes (a
get_by_text / role name / has_text / :has-text / text= value), and the base is one of the writer's own stable locators
(a test id, a plain data-* attribute, plain classes) that matches the element and at least one other.

Proof (the inventory is the oracle; the live check still has the last word): every element the base matches is listed
(the listing is complete), the element's text holds the call's text, and every other match's text does not. The text is
the probe's pw_text, read the way Playwright's has_text reads it (descendants' text joined with no separator, hidden text
included, case-insensitive, whitespace collapsed, a substring), not the spaced, visible-only `text` the pick uses: with
that one a row whose hidden duplicate holds the words would look clean. A match whose pw_text is missing (an older probe)
or incomplete (pw_cut: longer than the probe keeps, or holding a textarea, select or editable region) cannot be ruled
out, so the text is not used.

Features:
    ✓ call_texts(): the copy the broken call names (get_by_text, a role name, has_text, :has-text / :text-is / text= in a
      selector but not inside :not()), in order, without selectors or regexes
    ✓ text_filtered(): the narrowed locators of one element (WriteResult) or None
    ✓ the result is copy-based (a text tier), in both languages, round-tripped through the chain parser and the
      replacement validator

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from .element_facts import _chain_of, subject_tag
from .locator_policy import (_HOOK_KEYS, RANK, WriteResult, Written, _py, _swap_receiver, _ts, candidates, kind_of,
                             write_facts)
from .locator_steps import Chain, chain_to_data, try_parse
from .locators import validate_replacement

TEXT_CHARS = 80            # the longest call text used
MIN_TEXT_CHARS = 2
MAX_TEXTS = 8              # texts of the call tried
MAX_WRITTEN = 3
_IN_SELECTOR = re.compile(r""":(?:has-text|text-is|text)\(\s*(?:'([^'\\]*)'|"([^"\\]*)")\s*\)"""
                          r"""|(?:^|>>)\s*text\s*=\s*(?:'([^'\\]*)'|"([^"\\]*)"|([^'">][^>]*))""")
_TEXT_ARG = {"get_by_text", "getByText"}
# JavaScript's \s (Playwright collapses runs of it to one space); text holding a character only one of Python and
# JavaScript calls whitespace, or a zero-width space (Playwright drops it after trimming), is not used
_JS_WS = re.compile("[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+")
_UNSURE = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f\u180e\u200b]")      # and control characters


def _js_norm(s: str) -> str:
    return _JS_WS.sub(" ", s).strip(" ")


def _without_not(sel: str) -> str:
    """`sel` with every :not(...) taken out, however deeply its parentheses nest: text a selector rules out is not the
    element's. An unclosed :not( takes the rest of the selector with it."""
    out, i, n = [], 0, len(sel)
    while i < n:
        if sel[i] == "\\":                              # an escaped character is never a :not or a paren
            out.append(sel[i:i + 2])
            i += 2
            continue
        if sel[i:i + 5].lower() == ":not(":
            depth, quote, i = 1, "", i + 5
            while i < n and depth:
                c = sel[i]
                if quote:
                    quote = "" if c == quote else quote
                    i += 2 if c == "\\" else 1         # an escaped character never closes the quote
                    continue
                if c == "\\":
                    i += 2
                    continue
                quote = c if c in "'\"" else ""
                depth += {"(": 1, ")": -1}.get(c, 0)
                i += 1
            continue
        out.append(sel[i])
        i += 1
    return "".join(out)


def _from_chain(chain: Chain, out: List[str]) -> None:
    for s in chain.steps:
        if s.method in _TEXT_ARG and s.args and isinstance(s.args[0], str):
            out.append(s.args[0])
        if s.method in ("locator", "frame_locator", "frameLocator") and s.args and isinstance(s.args[0], str):
            for m in _IN_SELECTOR.finditer(_without_not(s.args[0])):
                out.append(next(g for g in m.groups() if g is not None))
        for key in ("name", "has_text", "hasText"):
            if isinstance(s.options.get(key), str):
                out.append(s.options[key])
        if isinstance(s.options.get("has"), Chain):
            _from_chain(s.options["has"], out)


def call_texts(call: str) -> List[str]:
    """The copy the broken call names (a regex is not copy, a selector is not copy), first seen first, deduplicated."""
    chain = _chain_of(call)
    if chain is None:
        return []
    found: List[str] = []
    _from_chain(chain, found)
    out: List[str] = []
    for t in found:
        if _UNSURE.search(t):
            continue
        t = _js_norm(t)
        if MIN_TEXT_CHARS <= len(t) < TEXT_CHARS and any(c.isalnum() for c in t) \
                and t.lower() not in [o.lower() for o in out]:
            out.append(t)
    return out[:MAX_TEXTS]


def _holds(el: Dict[str, Any], want: str) -> Optional[bool]:
    """Whether has_text would find `want` in the element: True only when the text Playwright reads (the probe's pw_text)
    holds it, None when that cannot be told (no pw_text, or pw_text is incomplete)."""
    text = el.get("pw_text")
    if not isinstance(text, str):
        return None
    if want.lower() in text.lower():
        return True
    return None if el.get("pw_cut") else False


def _lacks(el: Dict[str, Any], want: str) -> bool:
    """True only when has_text surely would NOT find `want` in the element: no case mapping of either side holds it."""
    text = el.get("pw_text")
    if not isinstance(text, str) or el.get("pw_cut"):
        return False
    return not any(f(want) in f(text) for f in (str.lower, str.casefold, str.upper))


def _bases(facts: Any, test_id_attribute: str, language: str, receiver: str, other: str,
           recv_other: str) -> List[Tuple[str, str, str, Optional[Tuple[str, str]]]]:
    """Each stable base as (code, the other language's code, the oracle's Python spelling, (attribute, value) for a test
    id hook or None for css), in the writer's order. The hook is paired by position, never read back out of the code."""
    hooks: List[Tuple[str, str]] = ([(test_id_attribute, facts.test_id)] if facts.test_id else []) + list(facts.test_attrs)
    out: List[Tuple[str, str, str, Optional[Tuple[str, str]]]] = []
    for kind in ("testid", "css"):
        trio = list(zip(candidates(facts, language, receiver, (kind,)), candidates(facts, other, recv_other, (kind,)),
                        candidates(facts, "python", "page", (kind,))))
        if kind == "testid" and len(trio) != len(hooks):
            continue                                       # not the pairing the writer makes: no test id base
        for i, (a, b, c) in enumerate(trio):
            out.append((a, b, c, hooks[i] if kind == "testid" else None))
    return out


def _matches(inv: Any, cand_page: str, hook: Optional[Tuple[str, str]]) -> Optional[List[Dict[str, Any]]]:
    """The listed elements the base matches: a test id from the key its attribute is listed under, css from the oracle."""
    if hook is None:
        return inv.matched(cand_page)
    key = _HOOK_KEYS.get(hook[0])
    return [e for e in inv.elements if e.get(key) == hook[1]] if key and hook[1] else None


def text_filtered(item: Dict[str, Any], inv: Any, language: str, receiver: str, broken_kind: str,
                  test_id_attribute: str, broken_call: str) -> Optional[WriteResult]:
    """`item`'s locators as a shared stable base narrowed by one of the broken call's texts, or None."""
    if not inv.complete:
        return None
    texts = [t for t in call_texts(broken_call) if _holds(item, t)]
    if not texts:
        return None
    py = language == "python"
    other = "typescript" if py else "python"
    facts = write_facts(item, test_id_attribute)
    recv_other = _swap_receiver(receiver, other)
    kept: List[Written] = []
    for cand, cand_other, cand_page, hook in _bases(facts, test_id_attribute, language, receiver, other, recv_other):
        if kind_of(cand_page) == "chain":
            continue
        subject = subject_tag(cand_page)
        if subject is not None and subject != (item.get("tag") or "").lower():
            continue
        matches = _matches(inv, cand_page, hook)
        if not matches or len(matches) < 2 or not any(e is item for e in matches):
            continue
        for t in texts:
            if not all(_lacks(e, t) for e in matches if e is not item):
                continue
            code = {language: _narrow(cand, t, py), other: _narrow(cand_other, t, not py)}
            chain = try_parse(code[language].replace(receiver, "page", 1), language)
            if chain is None or try_parse(code[other].replace(recv_other, "page", 1), other) is None:
                continue
            if validate_replacement(broken_call, code[language], language) is not None:
                continue
            kept.append(Written("text", RANK["text"], code, chain_to_data(chain), True))
            break
        if len(kept) >= MAX_WRITTEN:
            break
    if not kept:
        return None
    floor = RANK.get(broken_kind or "chain", RANK["chain"])
    return WriteResult(kept, not any(w.tier <= floor for w in kept), broken_kind or "chain")


def _narrow(base: str, text: str, py: bool) -> str:
    return f"{base}.filter(has_text={_py(text)})" if py else f"{base}.filter({{ hasText: {_ts(text)} }})"
