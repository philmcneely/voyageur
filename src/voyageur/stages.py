"""
===============================================================================
Stages (the CPU-heavy steps of a heal, as plain functions a worker process can run)
===============================================================================
Everything here reads client text (source, error, aria snapshot, inventory) and nothing else: no store, no model, no
lock, no clock. Each stage takes picklable arguments and returns a picklable result, so core.HealSession can run it
in-process (tests) or in a worker that deadline.ProcessRunner can kill when the request's time is spent. The model call
and every store write stay in core.py, in the parent.

Features:
    ✓ analyze(): triage, the inventory fingerprint and the same-element match (ladder steps 1 and 2); a step inside a frame
      is matched against that frame's elements only (frames.scope_of); a frame hop that names no listed iframe is healed first
      when the element it entered can be placed (frame_heal.heal_hop)
    ✓ pick_input(): the candidates shown to the model and the prompt (step 3, before the model call)
    ✓ vision_input(): the same candidates, the overlay drawn on the client's screenshot and the vision prompt (step 4, before its
      model call); the image is decoded here, in the worker, under the request deadline
    ✓ write_for(): the ranked locators of the settled element (step 5); when none is unique on the page, the ones unique
      inside its hooked row (row_scope: the row's own locators, an explicit role attribute, the tail of a generated id), else a
      shared stable locator narrowed by a text the broken call named (text_filter); after a healed frame hop, the step's own
      locator behind the new hop comes first (frame_heal.hop_candidates)
    ✓ plan_for(): the language branch alone (advise mode); patches_for(): the language branch, the patch for the broken call and the patches for the other uses (step 6)

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import difflib
import hashlib
import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Set, Tuple

from .branches import BranchPlan, plan as plan_branch
from .frame_heal import heal_hop, hop_candidates
from .frames import HopHeal, Scope, prefixed, scope_of
from .hashed_copy import reveal_all
from .inventory import Inventory
from .locator_policy import RANK, Written, WriteResult, write
from .locators import receiver_of, validate_replacement
from .match import match_fingerprint
from .overlay import Overlay, OverlayError, render
from .pick import build_prompt, candidate_line, context_lines, hint_of, prefilter
from .row_scope import SCOPED_ORDER, row_scope
from .schemas import HealRequest, Patch, Span
from .text_filter import text_filtered
from .triage import Triage, triage
from .vision import build_prompt as build_vision_prompt

MAX_OTHER_PATCHES = 20
log = logging.getLogger("voyageur")


@dataclass
class Analysis:
    """Steps 1 and 2: the triage, the flat inventory, its hash and the fingerprint match (ref and signals) if any."""
    tri: Triage
    flat: List[Dict[str, Any]]
    inv_hash: str
    matched: Optional[Tuple[str, List[str]]]
    scope: Optional[Scope] = None      # set when the broken call enters a frame: only that frame's elements are candidates
    hop: Optional[HopHeal] = None      # set when the call's frame hop was healed (its scope may be None: the element is in the page)


def analyze(req: HealRequest) -> Analysis:
    flat = req.page.flat_inventory()
    inv = Inventory(flat, req.page.inventory_more == 0, req.page.aria_snapshot or "", req.page.inventory_copy_more,
                    req.page.inventory_copy_withheld, req.page.inventory_copy_withheld_data)
    stack = [(f.file, f.line) for f in req.failure.stack]
    tri = triage(req.failure.error, req.source.files, stack, req.client.language)
    inv_hash = hashlib.sha256(json.dumps([{k: v for k, v in e.items() if k not in ("box", "ref")} for e in flat],
                                         sort_keys=True).encode()).hexdigest()
    matched = None
    fp, neighbours = reveal_all(req.last_pass.fingerprint.flat(), [n.flat() for n in req.last_pass.neighbours], flat) \
        if req.last_pass else (None, [])
    scope = scope_of(tri.call, flat, inv.complete, inv.copy_more) if tri.status == "located" else None
    hop = None
    if scope is not None and scope.frame is None and scope.writable:
        healed = heal_hop(tri.call, flat, inv.complete, fp, tri.action, tri.language, req.test_id_attribute, inv.copy_more,
                          inv.withheld_data)
        if healed.hop is not None:
            scope, hop = healed.scope, healed.hop
        else:
            scope.why = healed.why
    if scope is not None and scope.inventory is not None:
        scope.inventory.inherit(inv)
    if tri.status == "located" and fp:
        m = match_fingerprint(scope.elements if scope else inv.elements, fp, tri.action, neighbours,
                              () if scope else inv.in_frames)
        if m:
            matched = (m.element["ref"], list(m.signals))
    return Analysis(tri, flat, inv_hash, matched, scope, hop)


def pick_input(content: str, line: int, page_lang: str, elements: List[Dict[str, Any]], action: Optional[str],
               call: str, cap: int, excluded: Set[str], recorded: Optional[Dict[str, Any]] = None) -> Tuple[List[str], str]:
    """(refs of the candidates shown, the prompt); no refs when nothing is left to show."""
    shown = prefilter(elements, action, hint_of(call), cap, excluded, call, recorded)
    if not shown:
        return [], ""
    return [e["ref"] for e in shown], build_prompt(context_lines(content, line), page_lang,
                                                    [candidate_line(e) for e in shown])


@dataclass
class VisionInput:
    """What the vision call needs: the candidates shown (refs in mark order: mark n is refs[n - 1]), the prompt and the
    overlay; or, when no overlay could be made, `skip` (a reason code) and nothing else."""
    refs: List[str]
    prompt: str
    overlay: Optional[Overlay]
    skip: str = ""


def vision_input(content: str, line: int, page_lang: str, elements: List[Dict[str, Any]], action: Optional[str],
                 call: str, cap: int, excluded: Set[str], recorded: Optional[Dict[str, Any]], png: bytes,
                 viewport: Tuple[Optional[float], Optional[float], Optional[float]],
                 scroll: Tuple[float, float]) -> VisionInput:
    """The vision call's input for the same candidates the text pick would see (minus the refs already ruled out)."""
    shown = prefilter(elements, action, hint_of(call), cap, excluded, call, recorded)
    if not shown:
        return VisionInput([], "", None, "no_candidates")
    boxes = [(b["x"], b["y"], b["width"], b["height"]) if isinstance(b := e.get("box"), dict) else None for e in shown]
    try:
        overlay = render(png, boxes, viewport, scroll)
    except OverlayError as exc:
        return VisionInput([], "", None, exc.reason)
    except Exception as exc:                            # whatever a hostile image or number does, the heal goes on with text
        log.warning("overlay failed: %s", type(exc).__name__)       # the type only: never the message, never image bytes
        return VisionInput([], "", None, "overlay_error")
    prompt = build_vision_prompt(context_lines(content, line), page_lang, shown, set(overlay.drawn))
    return VisionInput([e["ref"] for e in shown], prompt, overlay)


def write_for(inv: Inventory, ref: str, language: str, receiver: str, broken_kind: str, test_id_attribute: str,
              broken_call: str, scope: Optional[Scope] = None, hop: Optional[HopHeal] = None) -> WriteResult:
    """The locators of element `ref`. For a step inside a frame the element is one of the frame's, the locators are written
    against the frame's own elements and each gets the broken call's frame hop in front. After a healed hop the step's own
    locator behind the new hop is offered first: it was not what broke, and the client's live check says whether it holds."""
    res = _written(inv, ref, language, receiver, broken_kind, test_id_attribute, broken_call, scope)
    kept = hop_candidates(hop, language, receiver, broken_call) if hop is not None else []
    if not kept:
        return res
    mine = {w.code[language] for w in kept}
    rest = [w for w in res.candidates if w.code[language] not in mine]
    return replace(res, candidates=kept[:1] + rest + kept[1:], below_floor=res.below_floor and bool(rest), reason="")


def _written(inv: Inventory, ref: str, language: str, receiver: str, broken_kind: str, test_id_attribute: str,
             broken_call: str, scope: Optional[Scope]) -> WriteResult:
    if scope is None:
        target = next(e for e in inv.elements if e["ref"] == ref)
        res = write(target, inv, language, receiver, broken_kind, test_id_attribute, broken_call=broken_call)
        if not res.candidates:
            res = _in_row(target, inv, language, receiver, broken_kind, test_id_attribute, broken_call) or res
        if res.candidates and not all(w.copy_based for w in res.candidates):
            return res
        # nothing stable is unique: a shared stable locator narrowed by the call's own text comes after the copy ones (a
        # container's listed text joins its cells with spaces, so get_by_text on it can miss live)
        narrowed = text_filtered(target, inv, language, receiver, broken_kind, test_id_attribute, broken_call)
        if narrowed is None:
            return res
        if not res.candidates:
            return narrowed
        floor = RANK.get(broken_kind or "chain", RANK["chain"])
        extra = [w for w in narrowed.candidates if res.below_floor or w.tier <= floor]
        return replace(res, candidates=res.candidates + extra)
    target = next(e for e in scope.elements if e["ref"] == ref)
    res = write(target, scope.inventory, language, receiver, broken_kind, test_id_attribute, broken_call=broken_call)
    if not scope.writable:
        return WriteResult([], False, res.floor_kind, "no_stable_locator")
    kept = []
    for w in res.candidates:
        moved = prefixed(w, scope.prefix, receiver, language)
        if moved is not None and validate_replacement(broken_call, moved.code[language]) is not None:
            moved = None
        if moved is not None:
            kept.append(moved)
    return WriteResult(kept, res.below_floor if kept else False, res.floor_kind, "" if kept else "no_stable_locator",
                       "" if kept else res.gap, "" if kept else res.why)


_CALL_CLASS = re.compile(r"""\.([A-Za-z_][A-Za-z0-9_-]*)""")
_QUOTED = re.compile(r"""'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)\"""")


def named_classes(call: str) -> Tuple[str, ...]:
    """Classes the broken call names in a selector string (`.MuiSelect-select`), without a digit in them: a class a person
    wrote into a locator and that the element still carries is a stable class even when it starts like a generated id (the
    `mui` prefix). Used only inside a hooked container, where the class must still be unique among the container's elements."""
    out: List[str] = []
    for m in _QUOTED.finditer(call[:4000]):
        for c in _CALL_CLASS.findall(m.group(1) if m.group(1) is not None else m.group(2)):
            if not re.search(r"\d", c) and c not in out:
                out.append(c)
    return tuple(out[:8])


def _in_row(target: Dict[str, Any], inv: Inventory, language: str, receiver: str, broken_kind: str,
            test_id_attribute: str, broken_call: str) -> Optional[WriteResult]:
    """When nothing is unique on the page: the locators unique inside the hooked row the element sits in, with the row's
    hook in front (row_scope), or None."""
    rs = row_scope(target, inv, language, receiver)
    if rs is None:
        return None
    prefix, prefix_other, sub = rs
    inner = next((e for e in sub.elements if e["ref"] == target["ref"]), None)
    if inner is None:
        return None
    res = write(inner, sub, language, receiver, broken_kind, test_id_attribute, SCOPED_ORDER, broken_call,
                named_classes(broken_call))
    kept = []
    for w in res.candidates:
        moved = prefixed(w, prefix, receiver, language, prefix_other)
        if moved is not None and validate_replacement(broken_call, moved.code[language]) is None:
            kept.append(replace(moved, scope=prefix[len(receiver) + 1:]))
    return WriteResult(kept, res.below_floor, res.floor_kind) if kept else None


def _patch(path: str, digest: str, content: str, start: int, end: int, old: str, new: str) -> Patch:
    after = content[:start] + new + content[end:]
    diff = "\n".join(difflib.unified_diff(content.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}",
                                          lineterm="", n=3))
    return Patch(file=path, base_sha256=digest, span=Span(start=start, end=end), **{"from": old}, to=new, diff=diff)


def _other_patches(tri: Triage, files: Dict[str, Tuple[str, str]], to: str,
                   branches: Optional[Dict[str, str]]) -> List[Patch]:
    out: List[Patch] = []
    for use in tri.uses[:MAX_OTHER_PATCHES]:
        digest, text = files[use.path]
        recv = receiver_of(use.text)
        moved = re.sub(r"(?<![\w.])" + re.escape(tri.receiver) + r"(?=[.,)])", lambda _m: recv, to)
        if ("python" if use.path.endswith(".py") else "typescript") != tri.language:
            continue                                    # a file in the other language is not given this code
        # a language switch is validated branch by branch (never skipped because the text names the helper); the
        # branch that is the call itself is the old language's and is allowed to equal it
        parts = [re.sub(r"(?<![\w.])" + re.escape(tri.receiver) + r"(?=[.,)])", lambda _m: recv, b)
                 for b in branches.values()] if branches else [moved]
        if any((why := validate_replacement(use.text, part, tri.language)) is not None
               and not (branches and why.startswith("identical")) for part in parts):
            continue
        out.append(_patch(use.path, digest, text, use.start, use.end, use.text, moved))
    return out


def plan_for(c: Written, tri: Triage, last_lang: str, page_lang: str, fingerprint: Optional[Dict[str, Any]],
             locale_branching: bool) -> BranchPlan:
    """The language plan for candidate `c` (advise mode asks for it without building a patch)."""
    return plan_branch(c, tri.language, tri.receiver, tri.call, last_lang, page_lang, fingerprint, locale_branching)


def patches_for(c: Written, tri: Triage, last_lang: str, page_lang: str, fingerprint: Optional[Dict[str, Any]],
                locale_branching: bool, content: str, files: Dict[str, Tuple[str, str]]
                ) -> Tuple[BranchPlan, Optional[Patch], List[Patch]]:
    """The language plan for candidate `c`; when it can be built, the patch for the broken call and the other uses'."""
    bp = plan_for(c, tri, last_lang, page_lang, fingerprint, locale_branching)
    if bp.needed and bp.code is None:
        return bp, None, []
    to = bp.code or c.code[tri.language]
    patch = _patch(tri.path, tri.digest, content, tri.start, tri.end, tri.call, to)
    return bp, patch, _other_patches(tri, files, to, bp.branches)
