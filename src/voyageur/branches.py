"""
===============================================================================
Language Branches (copy-based locators on a page in another language)
===============================================================================
A locator built from page copy (role + name, label, placeholder, text) only works in the language the copy is written in.
When the page now renders in a different language than the passing run did, writing the new copy over the old would fix
today's run and break every run in the other language, and translating the old copy is a guess. So the service keeps both:
it returns one locator per language and the code that picks between them from the page's language. The old language's
locator is the broken call itself when that was copy-based (it passed in that language), else it is written from the copy
the passing run recorded in its fingerprint; the new language's locator is the one written from the page as it is now.
Copy is never translated, and when the old copy is not known no branch is built at all (the caller ends the heal for a
human) rather than guessed.

A locator that does not depend on copy (a test id, an id, a name, a class selector) wins with no branch: it works in every
language.

Features:
    ✓ plan(): needed or not, the branches {language: code}, the switching code and its language-neutral form
    ✓ Switching code is a call to a helper the client ships: by_language(receiver, {"en": ..., "fr": ...}) in Python,
      byLanguage(receiver, { en: ..., fr: ... }) in TypeScript (no await, so it fits a getter or a field); it returns the entry for the page's language and
      the first entry (the original copy) for any other
    ✓ Language keys are plain two or three letter subtags only: the key is written into the test file
    ✓ The page language comes from the caller (html lang, then browser locale, then Content-Language), never from words
      on the page

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .locator_policy import _LANG, ElementFacts, Written, candidates, copy_based, page_language
from .locator_steps import chain_to_data, try_parse

HELPER = {"python": "by_language", "typescript": "byLanguage"}
UNAVAILABLE = "language_branch_unavailable"


@dataclass
class BranchPlan:
    """What the language check decided. `needed` False: use the candidate as it is. `needed` True with `code`: use the
    switching code. `needed` True with `reason` and no code: a branch is required and cannot be built."""
    needed: bool = False
    code: Optional[str] = None
    branches: Optional[Dict[str, str]] = None
    neutral: Optional[Dict[str, Any]] = None
    reason: str = ""


def _subtag(value: Optional[str]) -> str:
    return page_language({"lang": value or ""})


def _from_fingerprint(kind: str, fingerprint: Optional[Dict[str, Any]], language: str, receiver: str) -> Optional[str]:
    """The old-language locator of `kind` written from the copy a passing run recorded (never from the new page)."""
    if not isinstance(fingerprint, dict):
        return None

    def s(key: str) -> Optional[str]:
        v = fingerprint.get(key)
        return v if isinstance(v, str) and v else None
    facts = ElementFacts(tag=s("tag") or "", role=s("role"), accessible_name=s("name") or s("label"), label=s("label"),
                         placeholder=s("placeholder"), text=s("text"))
    found = candidates(facts, language, receiver, (kind,))
    return found[0] if found else None


def _neutral(code: str, language: str, receiver: str) -> Optional[Dict[str, Any]]:
    chain = try_parse(code.replace(receiver, "page", 1) if code.startswith(receiver + ".") else code, language)
    return None if chain is None else {"chain": chain_to_data(chain)}


def plan(candidate: Written, language: str, receiver: str, broken_call: str, last_language: Optional[str],
         page_lang: Optional[str], fingerprint: Optional[Dict[str, Any]] = None, enabled: bool = True) -> BranchPlan:
    """Whether `candidate` needs a language branch, and the branch when it does.

    Needed only when locale branching is on, both languages are known plain subtags, they differ and the candidate
    depends on copy. `broken_call` is the failing locator as written in the test; `fingerprint` is the passing run's
    recorded element (its copy is the old language's)."""
    old, new = _subtag(last_language), _subtag(page_lang)
    if not enabled or not old or not new or old == new or not candidate.copy_based:
        return BranchPlan()
    if not (_LANG.fullmatch(old) and _LANG.fullmatch(new)):
        return BranchPlan()
    new_code = candidate.code[language]
    old_code = broken_call if copy_based(broken_call) else _from_fingerprint(candidate.kind, fingerprint, language,
                                                                             receiver)
    if not old_code or old_code == new_code:
        # the same copy in both languages needs no branch; no old copy at all cannot be built, and is not guessed
        return BranchPlan() if old_code == new_code else BranchPlan(True, reason=UNAVAILABLE)
    old_n, new_n = _neutral(old_code, language, receiver), _neutral(new_code, language, receiver)
    if old_n is None or new_n is None:
        return BranchPlan(True, reason=UNAVAILABLE)
    helper = HELPER[language]
    if language == "python":
        code = f'{helper}({receiver}, {{"{old}": {old_code}, "{new}": {new_code}}})'
    else:
        code = f"{helper}({receiver}, {{ {old}: {old_code}, {new}: {new_code} }})"
    return BranchPlan(True, code, {old: old_code, new: new_code},
                      {"kind": "by_language", "branches": {old: old_n, new: new_n}})
