"""
===============================================================================
Hashed Copy (a fingerprint recorded with HEAL_FINGERPRINT_COPY=off, matched against the page's own copy)
===============================================================================
A client can record fingerprints without keeping element copy: with HEAL_FINGERPRINT_COPY=off each copy field holds
`sha256:<hex>` of the copy with case and spacing folded. A hash only answers "is this the same copy", so the service
hashes the copy of every element on the drifted page the same way and puts the page's copy back where a recorded hash
matches it. Every proof and refusal then reads the fingerprint as if the copy had been sent. A hash that matches no
element is left as it is: no element on the page says that copy any more, which is all a hash can tell (a copy that
only shares words with the recorded one, "Expiry" -> "Expiry date", is not seen; proof is then the stricter for it).

Features:
    ✓ hash_copy(): the client's formula (playwright-ai-framework utils/heal_fingerprints.hash_copy), byte for byte
    ✓ reveal_copy() / reveal_all(): a fingerprint (and its neighbours) with matched hashes replaced, the page's copy hashed
      once per request, each distinct string once; the same object when nothing in it is hashed
    ✓ a hash that two spellings fold to (Save / SAVE) stays unmatched, and an unmatched hash has no words (proof.words)
    ✓ only copy fields are looked at (name, label, placeholder, text, row, aria-label, title, alt): never ids or test ids

Environment Variables:
    None (the switch is the client's: HEAL_FINGERPRINT_COPY).

Author: PMAC
===============================================================================
"""
import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .bounds import MAX_FIELD_CHARS

COPY_FIELDS = ("name", "label", "placeholder", "text", "row", "aria_label", "title", "alt")
_HASHED = re.compile(r"sha256:[0-9a-f]{64}")


def hash_copy(text: Any) -> str:
    """`sha256:<hex>` of `text` cut to the client's field cap, with case and spacing folded."""
    return "sha256:" + hashlib.sha256(" ".join(str(text)[:MAX_FIELD_CHARS].split()).casefold().encode("utf-8")).hexdigest()


def is_hashed(value: Any) -> bool:
    return isinstance(value, str) and _HASHED.fullmatch(value) is not None


def page_copy(elements: Iterable[Dict[str, Any]], wanted: Set[str]) -> Dict[str, Optional[str]]:
    """hash -> the page copy that hashes to it, for the `wanted` hashes found; each distinct copy string is hashed once.
    A hash two spellings fold to (Save / SAVE) maps to None: which one was recorded is not known, so it stays unmatched."""
    found: Dict[str, Optional[str]] = {}
    done: Set[str] = set()
    for e in elements:
        for k in COPY_FIELDS:
            v = e.get(k)
            if not isinstance(v, str) or not v or v in done:
                continue
            done.add(v)
            h = hash_copy(v)
            if h in wanted:
                found[h] = v if found.get(h, v) == v else None
    return found


def _hashes(fp: Optional[Dict[str, Any]]) -> Set[str]:
    return {fp[k] for k in COPY_FIELDS if is_hashed(fp.get(k))} if fp else set()


def reveal_copy(fp: Optional[Dict[str, Any]], elements: Iterable[Dict[str, Any]],
                found: Optional[Dict[str, Optional[str]]] = None) -> Optional[Dict[str, Any]]:
    """`fp` with each hashed copy field replaced by the page copy that hashes the same, or `fp` itself if none is hashed.
    `found` is a page_copy() table built once for the request; without it one is built for `fp` alone."""
    wanted = _hashes(fp)
    if not wanted:
        return fp
    if found is None:
        found = page_copy(elements, wanted)
    out = dict(fp)
    for k in COPY_FIELDS:
        if is_hashed(out.get(k)) and found.get(out[k]):
            out[k] = found[out[k]]
    return out


def reveal_all(fp: Optional[Dict[str, Any]], neighbours: Sequence[Dict[str, Any]],
               elements: Sequence[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """The fingerprint and its neighbours revealed against one hashing of the page's copy."""
    wanted = _hashes(fp).union(*(_hashes(n) for n in neighbours))
    if not wanted:
        return fp, list(neighbours)
    found = page_copy(elements, wanted)
    return reveal_copy(fp, elements, found), [reveal_copy(n, elements, found) for n in neighbours]
