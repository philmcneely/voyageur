"""
===============================================================================
Overlay Marks (POST /v1/overlay: locators drawn on a screenshot, with no heal and no model)
===============================================================================
Given the page a client captured (its viewport screenshot, viewport, scroll and the probe's inventory) and, optionally, locator
expressions as written in tests, answer with the screenshot marked: a colored box on each element a locator matches, and a legend
saying what every mark is. It is the picture a heal shows a vision model, made on request and shown to a person (or to a tool
that wants to see where its locators land); the service itself looks at nothing and sends nothing anywhere.

    locators sent   each is resolved against the inventory by the matcher an audit uses (audit._hits: Inventory.matched, with
                    the count as a cross-check). Its number is its position in the request (1 is the first), whatever else
                    happens, so a caller can read the legend by index. status:
                      matched      exactly one listed element, and it is on the picture
                      not_visible  exactly one listed element, but it has no box or is mostly outside the screenshot
                      ambiguous    more than one (`count`); every match the picture holds is marked with the locator's number
                      not_found    none
                      unknown      the inventory cannot say (a scoped chain, a regex, a frame, a call over 1000 characters,
                                   or one element the listing does not show)
    none sent       every listed element that is not hidden and is on the picture is marked, numbered in listing order, and
                    labelled with the locator the service would write for it (stages.write_for: the same candidates, uniqueness
                    proof and stability ranking a heal uses). That is every element a test could target, not only the controls:
                    headings, labels, paragraphs and status text, sections and landmarks the probe lists, as well as inputs,
                    buttons and links. Each entry says which it is (`element_kind`: interactive or text, see element_kind()) and
                    `options.kinds` narrows the set (default both). An element with no unique stable locator is marked all the
                    same, status no_stable_locator, so a brittle spot is visible. The writer proves a text locator from the
                    probe's copy counts and a role locator from `page.aria_snapshot`: a client that sends neither gets fewer

Labels (`options.labels`): `numbers` draws the mark's number and the legend ties it to the locator; `locators` draws the locator
itself (overlay.clean_label: ASCII only, control characters removed, at most 48 characters, and no wider than the picture) and
the legend is returned as well, its `locator` always whole and its `label` the text that is on the picture (`label_truncated`
says it is shorter). Boxes that overlap (a section and what it holds) get different colors, said in the legend as `color`.

Features:
    ✓ run_overlay(): a module-level function over a parsed request, so a killable worker runs it under the request deadline
    ✓ The legend of every request: number, locator, label, status, count, ref(s), how many boxes were drawn, color, kind, tier
      (the locator's stability rank), copy_based and, with no locators sent, element_kind
    ✓ Bounded: at most 50 boxes per locator, 100 locators (schemas), 500 elements; the work budget (HEAL_AUDIT_BUDGET_S) ends the
      matching with `truncated` true and the legend of the locators done, never an error
    ✓ Deterministic: the same request gives the same bytes (overlay.render_marks)
    ✓ A picture that does not fit its viewport, or cannot be decoded, is `{"refused": <reason>}` (the route says 422)
    ✓ Pure: no model, no store, no log of the image or the locators

Environment Variables:
    None here (the work budget comes from HEAL_AUDIT_BUDGET_S via the core).

Author: PMAC
===============================================================================
"""
import base64
import logging
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .audit import MAX_AUDIT_EXPR, _hits
from .inventory import Inventory
from .locator_policy import RANK, kind_of
from .overlay import Mark, OverlayError, clean_label, render_marks, visible_boxes
from .schemas import OverlayRequest
from .screenshot import png_size
from .stages import write_for

MAX_BOXES_PER_LOCATOR = 50       # boxes drawn for one ambiguous locator (its `count` still says how many it matches)
_KIND_NAME = {"testid": "test_id"}
log = logging.getLogger("voyageur")


ELEMENT_KINDS = ("interactive", "text")
_ACTIVE_ROLES = frozenset({"button", "link", "textbox", "searchbox", "checkbox", "radio", "switch", "combobox", "listbox", "option",
                           "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "slider", "spinbutton", "treeitem", "scrollbar"})
_ACTIVE_TAGS = frozenset({"a", "button", "input", "select", "textarea", "summary", "option"})
_TEXT_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "label", "legend", "li", "ul", "ol", "dl", "dt", "dd", "section",
                        "article", "aside", "nav", "header", "footer", "main", "form", "fieldset", "figure", "figcaption", "caption",
                        "pre", "blockquote", "table", "thead", "tbody", "tr", "td", "th", "output", "time", "address", "details",
                        "dialog", "img", "svg"})


def element_kind(el: Dict[str, Any]) -> str:
    """`interactive` for what a person acts on (a form control, a button, a link, an element with a control's role, or one the
    probe listed because something acts on it) and `text` for the rest: headings, labels, paragraphs, status text, and sections
    or landmarks with a role or a name, the things a test asserts on."""
    role, tag = str(el.get("role") or "").lower(), str(el.get("tag") or "").lower()
    if role in _ACTIVE_ROLES or tag in _ACTIVE_TAGS:
        return "interactive"
    if el.get("copy_only") or role or tag in _TEXT_TAGS:
        return "text"
    return "interactive"             # a div or span with no copy and no role is listed only when a handler, tabindex or pointer cursor says so


def _box(el: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    b = el.get("box")
    return (b["x"], b["y"], b["width"], b["height"]) if isinstance(b, dict) else None


def _entry(number: int, locator: Optional[str], status: str, count: Optional[int], refs: List[str], kind: Optional[str] = None,
           copy_based: bool = False) -> Dict[str, Any]:
    return {"number": number, "locator": locator, "label": None, "status": status, "count": count,
            "ref": refs[0] if len(refs) == 1 and count == 1 else None, "refs": refs, "visible": 0, "color": None,
            "label_truncated": False, "element_kind": None,
            "kind": _KIND_NAME.get(kind, kind) if kind else None, "tier": RANK[kind] if kind else None, "copy_based": copy_based}


def _resolve(inv: Inventory, number: int, expr: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """The legend entry for locator `expr` and the elements it matched that the listing names (the ones to mark)."""
    shown, call = expr, expr.strip()                       # the legend echoes what was sent; the matcher reads it trimmed
    kind = kind_of(call)
    hits = _hits(inv, call) if len(call) <= MAX_AUDIT_EXPR else None
    if hits is None:
        return _entry(number, shown, "unknown", None, [], kind), []
    named = [h for h in hits if h.get("ref")][:MAX_BOXES_PER_LOCATOR]
    refs = [h["ref"] for h in named]
    if not hits:
        return _entry(number, shown, "not_found", 0, [], kind), []
    if len(hits) > 1:
        return _entry(number, shown, "ambiguous", len(hits), refs, kind), named
    return _entry(number, shown, "matched" if named else "unknown", 1, refs, kind), named


def _written(inv: Inventory, number: int, el: Dict[str, Any], language: str, test_id_attribute: str) -> Dict[str, Any]:
    """The legend entry for element `el` labelled with the locator the service would write for it."""
    try:
        res = write_for(inv, el["ref"], language, "page", "chain", test_id_attribute, "")
    except Exception:                                    # a shape the writer cannot take is an element with no locator
        res = None
    if res is None or not res.candidates:
        return _entry(number, None, "no_stable_locator", 1, [el["ref"]])
    best = res.candidates[0]
    return _entry(number, best.code[language], "matched", 1, [el["ref"]], best.kind, best.copy_based)


def run_overlay(req: OverlayRequest, budget_s: Optional[float] = None) -> Dict[str, Any]:
    """The POST /v1/overlay answer for `req`, stopping the matching at `budget_s` seconds (`truncated` true, the locators done
    in the legend). `{"refused": reason}` when the picture cannot be marked (viewport_mismatch, no_viewport, undecodable,
    overlay_error). A module-level function: it can run in a worker process."""
    started = time.monotonic()

    def out_of_time() -> bool:
        return budget_s is not None and time.monotonic() - started > budget_s
    shot, view = req.page.screenshot, req.page.viewport
    viewport = (view.w, view.h, view.dpr) if view else (None, None, None)
    scroll = (view.scroll.x, view.scroll.y) if view and view.scroll else (0.0, 0.0)
    inv = Inventory(req.page.flat_inventory(), req.page.inventory_more == 0, req.page.aria_snapshot or "",
                    req.page.inventory_copy_more, req.page.inventory_copy_withheld, req.page.inventory_copy_withheld_data)
    language = req.client.language if req.client else "python"
    legend: List[Dict[str, Any]] = []
    marks: List[Mark] = []
    truncated = False
    skipped = 0                                          # elements left unmarked when none were named: hidden, off the picture, in a frame
    try:
        if req.locators is not None:
            for number, expr in enumerate(req.locators, start=1):
                if out_of_time():
                    truncated = True
                    break
                entry, named = _resolve(inv, number, expr)
                legend.append(entry)
                marks.extend(Mark(number, _box(h)) for h in named)
        else:
            wanted = set(req.options.kinds or ELEMENT_KINDS)
            shown = [e for e in inv.elements if not e.get("hidden") and element_kind(e) in wanted]
            fits = visible_boxes(png_size(shot.png()), [_box(e) for e in shown], viewport, scroll)
            for el, on_picture in zip(shown, fits):
                if not on_picture:
                    continue
                if out_of_time():
                    truncated = True
                    break
                entry = _written(inv, len(legend) + 1, el, language, req.test_id_attribute)
                entry["element_kind"] = element_kind(el)
                legend.append(entry)
                marks.append(Mark(entry["number"], _box(el)))
            skipped = len(inv.elements) + len(inv.in_frames) - len(legend)
        if req.options.labels == "locators":
            texts = {e["number"]: clean_label(e["locator"] or "") for e in legend}
            marks = [Mark(m.number, m.box, texts.get(m.number, "")) for m in marks]
        picture = render_marks(shot.png(), marks, viewport, scroll, req.options.labels)
    except OverlayError as exc:
        return {"refused": exc.reason}
    except Exception as exc:                             # whatever a hostile image or number does, the route answers 422
        log.warning("overlay failed: %s", type(exc).__name__)      # the type only: never the message, never image bytes
        return {"refused": "overlay_error"}
    shown_boxes, colors, drawn_text = dict(picture.boxes), dict(picture.colors), dict(picture.texts)
    for e in legend:
        e["visible"] = shown_boxes.get(e["number"], 0)
        e["color"] = colors.get(e["number"])
        if req.options.labels == "locators" and e["locator"]:
            # the text that is on the picture (or would be: a locator with no box is drawn nowhere); the legend's `locator` is whole
            e["label"] = drawn_text.get(e["number"], clean_label(e["locator"])) or None
            e["label_truncated"] = e["label"] != clean_label(e["locator"], len(e["locator"]) + 3)
        if e["visible"] == 0 and e["status"] == "matched":
            e["status"] = "not_visible"
    return {"image": {"media_type": "image/png", "data": base64.b64encode(picture.png).decode("ascii"),
                      "width": picture.width, "height": picture.height},
            "labels": req.options.labels, "legend": legend,
            "summary": {"locators": len(legend), "marked": sum(1 for e in legend if e["visible"]),
                        "not_visible": sum(1 for e in legend if e["status"] == "not_visible") if req.locators is not None else skipped,
                        "by_status": dict(Counter(e["status"] for e in legend)),
                        "by_element_kind": dict(Counter(e["element_kind"] for e in legend if e["element_kind"])),
                        "inventory": len(req.page.inventory),
                        "inventory_complete": inv.complete},
            "truncated": truncated}
