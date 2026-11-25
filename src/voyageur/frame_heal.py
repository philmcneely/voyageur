"""
===============================================================================
Frame Hop Healing (the iframe a step enters changed; the element in it, or the step, did not)
===============================================================================
A step such as page.frame_locator("iframe[name^='Apx']").get_by_role("textbox", name="Card number") breaks at its first call
when the iframe is renamed, or removed so that its content sits in the page itself. frames.py cannot pool the candidates of
a frame it cannot find, so the heal used to end there. This module heals the hop and leaves the rest of the step alone.

The hop is the broken part only when its selector can be read (ids, names, titles, test hooks), names no listed iframe, and
the inventory is complete. Then every place an element could now be is looked at: the page itself and each listed frame. A place
"holds the step's element" when an element in it is proven to be the one the step meant, by what identity proof already
accepts and nothing weaker (proof.py): with a recording, an unchanged stable attribute, the same copy and kind, or the step's own
unchanged selector selecting it alone; without one, the step's own hook values or copy. Exactly one place must hold it. Two
(a card number field in a billing frame and in a shipping frame) or none end the heal as before. A frame is not chosen for
being "the only iframe nearby": it is chosen for holding a proven element, and the live check that follows (one match, this
element, the test passes) still has the last word.

Only a recording's unchanged stable attribute, or its selector proof, is strong. Anything else (the same copy; every proof
without a recording) says only that some element looks like the step's, which a field of an unrelated frame can too, so it
never drops the hop to the page (a header "Pay" button is not the checkout frame's), and for a frame it needs continuity: a
meaningful word of the old hop's selector (a name stem, a title) that the new iframe's id, name or title still carries.

When the place is a frame, the new hop is written for its iframe element: a test id or other hook, a hand-written id, a name that
is not a counter or random suffix, a plain data-* attribute, the title, and last a counter-like name, each unique among the
iframes beside it. Hops before it (a parent frame) are kept. When the place is the page (only for a step with a single hop) the
hop is dropped. What follows the hop is offered unchanged first, in both languages: the test author's locator was not what
broke; the locator written for the element in its new place follows as the fallback (stages.write_for).

Features:
    ✓ heal_hop(): HopOutcome(scope, hop, why) for a step whose last frame hop names no listed iframe; scope is None when the
      element is now in the page itself
    ✓ frame_selectors(): the selectors that name one iframe among its siblings, best first, quoted for the client language
    ✓ unstable_name(): a frame name that ends in a counter or has a random-looking suffix (`layui-layer-iframe2`, `Frame_k3j2h`)
    ✓ hop_candidates(): the Written locators for the healed hop with the step's own locator after it, both languages
    ✓ A place that is ambiguous, unnamable, at a depth the parent hops do not reach, held by a weak proof only and either the
      page or a frame sharing no word with the old hop, is refused with a sentence that says so

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .elements import compatible, stable_keys
from .frames import HopHeal, Scope, _names_of, hop_absent, last_hop
from .inventory import Inventory, possibly_cut
from .locator_policy import (COPY_KINDS, RANK, Written, _attr_value, _data_attrs, _id_selector, _py, _swap_receiver, _ts,
                             copy_based, is_generated_id, kind_of, render_chain)
from .locator_steps import chain_to_data, try_parse
from .locators import validate_replacement
from .proof import broken_facts, prove, words

_PAGE = ""
_WITH_RECORDING = frozenset({"identity", "copy", "selector"})      # the proofs that may place an element in a frame or the page
_WITHOUT_RECORDING = frozenset({"hook_stem", "copy"})
_WEAK = "copy"                 # says the same, which another frame's field can too; a stable attribute or hook outranks it
_GENERIC = frozenset({"iframe", "frame", "frames", "widget", "form"})      # words every frame name has: no continuity
_HOOKS = (("data-testid", "testid"), ("data-qa", "data_qa"), ("data-cy", "data_cy"), ("data-test", "data_test"))
_COUNTER = re.compile(r"\d$")
_TOKEN = re.compile(r"[^A-Za-z0-9]+")
MAX_DEPTH = 8                  # frames above one frame that are followed (the probe lists same-origin frames only)
HERE = "heal the frame locator first"


@dataclass
class FrameSelector:
    """One selector that names an iframe among its siblings: `selector` as the hop's argument reads, `kind` what it rests on."""
    selector: str
    kind: str


@dataclass
class HopOutcome:
    """What healing the hop found: the Scope of the frame the step now enters (None: the element is in the page), the HopHeal
    (None: nothing was healed) and, when nothing was, what is missing (empty when the hop was not the broken part)."""
    scope: Optional[Scope] = None
    hop: Optional[HopHeal] = None
    why: str = ""


def unstable_name(value: str) -> bool:
    """A name a script or a counter makes: it ends in a digit, has a letters-and-digits token (`k3j2h`), or looks generated."""
    if is_generated_id(value) or _COUNTER.search(value):
        return True
    return any(len(t) >= 4 and re.search(r"\d", t) and re.search(r"[A-Za-z]", t) for t in _TOKEN.split(value))


def frame_key(iframe: Dict[str, Any]) -> str:
    """The name the probe gives the elements inside this iframe (their `frame`): its name, else its id, else `iframe`."""
    return str(iframe.get("name_attr") or iframe.get("id") or "iframe")[:80]


def frame_selectors(frame: Dict[str, Any], siblings: List[Dict[str, Any]], test_id_attribute: str,
                    language: str) -> List[FrameSelector]:
    """The selectors for iframe `frame` among `siblings` (the iframes in the same document, it included), best first: test id
    attribute, other hooks, a hand-written id, a stable name, a plain data-* attribute, the title, a counter-like name. Only a
    selector exactly one sibling satisfies is returned."""
    py = language == "python"
    q = lambda attr, value: f"[{attr}={_attr_value(value, py)}]"      # noqa: E731
    found: List[Tuple[str, str, str, str]] = []                        # (selector, kind, inventory key, value)
    hooks = sorted(_HOOKS, key=lambda h: h[0] != test_id_attribute)
    for attr, key in hooks:
        if frame.get(key):
            found.append((q(attr, frame[key]), "testid", key, frame[key]))
    if frame.get("id") and not is_generated_id(frame["id"]):
        found.append((_id_selector(frame["id"], py), "id", "id", frame["id"]))
    name = frame.get("name_attr")
    if name and not unstable_name(name):
        found.append((f"iframe{q('name', name)}", "name", "name_attr", name))
    for attr, value in _data_attrs(frame):
        found.append((f"iframe{q(attr, value)}", "css", "data_" + attr[5:].replace("-", "_"), value))
    if frame.get("title") and not possibly_cut(frame, "title"):         # a title as long as the probe's cut may be the head of a longer one
        found.append((f"iframe{q('title', frame['title'])}", "title", "title", frame["title"]))
    if name and unstable_name(name):
        found.append((f"iframe{q('name', name)}", "name", "name_attr", name))
    return [FrameSelector(sel, kind) for sel, kind, key, value in found
            if [s for s in siblings if s.get(key) == value] == [frame]]


def _hop_code(selector: str, language: str) -> str:
    return f".frame_locator({_py(selector)})" if language == "python" else f".frameLocator({_ts(selector)})"


def _depth(key: str, iframes: Dict[str, List[Dict[str, Any]]]) -> int:
    """How many frames are entered to reach the frame `key` (1 for a frame in the page); a large number when a frame on the
    way is not listed or shares its key with another (the elements of the two cannot be told apart)."""
    depth = 0
    while key:
        found = iframes.get(key, [])
        depth += 1
        if len(found) != 1 or depth > MAX_DEPTH:
            return MAX_DEPTH + 1
        key = str(found[0].get("frame") or _PAGE)
    return depth


def _names_by_hook(call: str, fingerprint: Optional[Dict[str, Any]]) -> bool:
    """Whether the step or its recording names the element by a stable attribute (id, name, test hook): the same copy in
    another frame then does not make that frame the step's, because the element lacks what named it."""
    if fingerprint:
        return any(fingerprint.get(k) and not (k == "id" and is_generated_id(str(fingerprint[k])))
                   for k in stable_keys(fingerprint, fingerprint))
    return bool(broken_facts(call).values)


def _holders(pool: List[Dict[str, Any]], call: str, fingerprint: Optional[Dict[str, Any]], action: Optional[str],
             inner: str, complete: bool, cut: bool, withheld: bool = False) -> Tuple[bool, bool]:
    """(a strong proof, any proof) that an element of one place is the step's: what identity proof accepts of it. Strong is
    a recording's unchanged stable attribute or selector proof; the same copy, and every proof without a recording, is weak,
    and the same copy counts only for a step that names its element by no stable attribute. `cut`: the probe left copy-holding
    elements out, which the selector proof is told by marking each element."""
    items = [dict(e, copy_cut=True) if cut else e for e in pool if not e.get("hidden")]
    allowed = _WITH_RECORDING if fingerprint else _WITHOUT_RECORDING
    if withheld or _names_by_hook(call, fingerprint):
        allowed = allowed - {_WEAK}               # withheld: the same copy may sit unlisted in another place too
    kinds = set()
    for e in items:
        if action and not compatible(action, e):
            continue
        proof, _ = prove(e, fingerprint, call, items, kept=inner, complete=complete)
        if proof is not None and proof[0] in allowed:
            kinds.add(proof[0])
    return bool(fingerprint and kinds - {_WEAK}), bool(kinds)


def continuous(selector: str, frame: Dict[str, Any]) -> bool:
    """Whether the new iframe still carries something of the old hop's selector: a meaningful word (not `iframe`) shared by a
    value the selector names (id, name, title, hook) and the frame's id, name, title or hook. Numbers say nothing."""
    old: set = set()
    for _op, value in _names_of(selector):
        old |= words(value)
    now: set = set()
    for k in ("id", "name_attr", "title", "testid", "data_qa", "data_cy", "data_test"):
        now |= words(frame.get(k))
    return bool((old & now) - _GENERIC)


def _place(key: str) -> str:
    return "the page" if key == _PAGE else f"the frame {key[:40]}"


def heal_hop(call: str, flat: List[Dict[str, Any]], complete: bool, fingerprint: Optional[Dict[str, Any]],
             action: Optional[str], language: str, test_id_attribute: str, copy_more: int = 0,
             withheld_data: int = 0) -> HopOutcome:
    """Heal the last frame hop of `call`: see the module header. `flat` is every inventory entry, `fingerprint` the recorded
    element (flat form) or None. `copy_more` is the probe's count of copy-holding elements it left out, `withheld_data` of the
    short copy it withheld inside options and data widgets (the same copy there is unlisted: the "same copy" proof says nothing
    about how many places hold it). HopOutcome() with an
    empty `why` when the hop is not shown to be the broken part."""
    hop = last_hop(call)
    if hop is None or hop[0] < 0 or not call[hop[1]:].strip():
        return HopOutcome()
    start, end, selector, _ = hop
    if not hop_absent(selector, flat, complete):
        return HopOutcome()
    inner, before = call[end:], call[:start]
    places: Dict[str, List[Dict[str, Any]]] = {}
    for e in flat:
        places.setdefault(str(e.get("frame") or _PAGE), []).append(e)
    cut = bool(copy_more) or any(e.get("copy_cut") for e in flat)
    proofs = {key: _holders(pool, call, fingerprint, action, inner, complete and not cut, cut, bool(withheld_data))
              for key, pool in places.items()}
    held = [k for k, (strong, _) in proofs.items() if strong] or [k for k, (_, any_) in proofs.items() if any_]
    if not held:
        return HopOutcome()
    if len(held) > 1:
        names = ", ".join(_place(k) for k in sorted(held))
        return HopOutcome(why=f"the step's element could be in more than one place ({names}): {HERE}")
    key = held[0]
    weak = not proofs[key][0]
    cannot = f"the element is in {_place(key)}, which the hop cannot be rewritten to reach: {HERE}"
    if weak and key == _PAGE:
        return HopOutcome(why=f"an element of the page looks like the step's, but nothing shows it is the one that was in the "
                              f"frame, and the hop is not dropped on that: {HERE}")
    hops_before = len(re.findall(r"\.(?:frame_locator|frameLocator)\(", before))
    old = call[:end]
    if key == _PAGE:
        if hops_before:
            return HopOutcome(why=cannot)
        note = "The frame the step entered is gone; the element is in the page itself and the step's own locator finds it."
        return HopOutcome(None, HopHeal(old, inner, [before], note))
    iframes: Dict[str, List[Dict[str, Any]]] = {}
    for e in flat:
        if e.get("tag") == "iframe":
            iframes.setdefault(frame_key(e), []).append(e)
    if _depth(key, iframes) != hops_before + 1:
        return HopOutcome(why=cannot)
    frame = iframes[key][0]
    if weak and not continuous(selector, frame):
        return HopOutcome(why=f"an element of {_place(key)} looks like the step's, but nothing shows that frame is the one the "
                              f"hop named (no word of the old selector is in its id, name or title): {HERE}")
    parent = str(frame.get("frame") or _PAGE)
    siblings = [e for e in flat if e.get("tag") == "iframe" and str(e.get("frame") or _PAGE) == parent]
    written = frame_selectors(frame, siblings, test_id_attribute, language)
    if not written:
        return HopOutcome(why=f"the frame holding the element has nothing stable to name it by: {HERE}")
    prefixes = [before + _hop_code(w.selector, language) for w in written]
    inside = [{k: v for k, v in e.items() if k != "frame"} for e in places[key]]
    inv = Inventory(inside, complete, "", max(copy_more, 1 if cut else 0))
    note = (f"The frame the step entered was renamed or replaced; the element is in {_place(key)}, named by its "
            f"{written[0].kind}, and the step's own locator finds it there.")
    healed = HopHeal(old, inner, prefixes, note)
    return HopOutcome(Scope(prefixes[0], key, inv.elements, inv, True, healed), healed)


def hop_candidates(hop: HopHeal, language: str, receiver: str, broken_call: str) -> List[Written]:
    """The healed hop with the step's own locator after it, one per hop spelling, as Written locators in both languages. A
    spelling that does not parse, has no rendering in the other language or fails the replacement validator is left out."""
    other = "typescript" if language == "python" else "python"
    out: List[Written] = []
    for prefix in hop.prefixes:
        code = prefix + hop.inner
        chain = try_parse(code.replace(receiver, "page", 1), language)
        theirs = render_chain(chain, other, _swap_receiver(receiver, other)) if chain is not None else None
        if chain is None or theirs is None or validate_replacement(broken_call, code, language) is not None:
            continue
        kind = kind_of(code)
        out.append(Written(kind, RANK[kind], {language: code, other: theirs}, chain_to_data(chain), kind in COPY_KINDS
                           or copy_based(code)))
    return out
