"""
===============================================================================
Fingerprint Match (the same element again, without a model)
===============================================================================
When the last passing run left a fingerprint of the element a locator hit, most small drifts can be settled by code: an id
was renamed but the name attribute stayed, a test id was removed but the role, name and place on the page did not move.
This module scores the page inventory against that fingerprint and settles the target only when exactly one element is a
strong match. Two strong matches, or none, settle nothing and the heal moves on to the text pick; a guess here would be a
wrong-element heal with no model to blame.

Strong means one of:
    - a stable attribute (id, name attribute, test id, data-qa / data-cy or another data-* hook) with the same non-empty
      value as the fingerprint's, judged by elements.identity_match (a hook many elements share, like a row menu button,
      identifies only together with its row); a generated id never counts
    - the same ARIA role and accessible name as the fingerprint and the same place on the page (overlapping box)
Hidden elements never match, and neither does an element the test's action cannot be taken on (fill on a div), or one the
recorded-case rules of gates.refusal_for refuse (the renamed neighbour of a removed element, a page-level twin of an element
that moved into a frame): a strong match is still a choice, and is checked like one.

Features:
    ✓ match_fingerprint(elements, fingerprint, action): Match(element, signals) or None
    ✓ signals say why it matched (the attribute names, or role+name+box), for the heal record and the PR body
    ✓ Pure and fast: no model, no network, no page

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .elements import box_of, compatible, identity_match, norm_text, same_place, stable_keys
from .gates import refusal_for
from .inventory import _role_of
from .locator_policy import is_generated_id


@dataclass
class Match:
    """The one element that matched and the signals that made it strong."""
    element: Dict[str, Any]
    signals: List[str] = field(default_factory=list)


def _signals(e: Dict[str, Any], fp: Dict[str, Any], items: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    base = dict(fp)
    base.setdefault("tag", e.get("tag") or "")
    shared = [k for k in stable_keys(e, base) if e.get(k) and e.get(k) == base.get(k)
              and not (k == "id" and is_generated_id(e[k]))]
    if shared and identity_match(e, base, [i for i in items if i is not e]):
        out += shared
    role, name = norm_text(fp.get("role")), norm_text(fp.get("name"))
    mine, theirs = box_of(e.get("box")), box_of(fp.get("box"))
    if role and name and _role_of(e) == role and name in (norm_text(e.get("name")), norm_text(e.get("label"))) \
            and mine and theirs and same_place(mine, theirs):
        out.append("role+name+box")
    return out


def match_fingerprint(elements: List[Dict[str, Any]], fingerprint: Dict[str, Any],
                      action: Optional[str] = None, neighbours: Sequence[Dict[str, Any]] = (),
                      framed: Sequence[Dict[str, Any]] = ()) -> Optional[Match]:
    """The one element of `elements` that is strongly the fingerprinted one, or None (none, or several)."""
    if not fingerprint:
        return None
    strong = []
    for e in elements:
        if e.get("hidden") or (action and not compatible(action, e)):
            continue
        sig = _signals(e, fingerprint, elements)
        if sig and refusal_for(e, elements, fingerprint, neighbours, framed) is None:
            strong.append(Match(e, sig))
    return strong[0] if len(strong) == 1 else None
