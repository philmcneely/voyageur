"""
===============================================================================
Element Identity (is this the same element as that one?)
===============================================================================
Small, pure helpers over inventory elements (the dicts the page probe produces). They answer the questions a heal keeps
asking without a browser: where is an element (its box), is it in the same place as another, do two entries describe the
same element by their stable attributes (id, name, test hooks, any other data-* attribute) whatever their copy says, can
a given action be taken on it, and what is a one-line description of it for a reason message. They also hold the one
fail-closed question a heal must never get wrong: could a control found now be one that was already on the page when the
test passed? If yes it must not be taken for a new one.

Features:
    ✓ box_of: a page box as (x, y, w, h), or None for a missing, non-finite, negative or zero-sized one
    ✓ norm_text: the one normaliser (whitespace collapsed, trimmed, case-folded) so copy compares the same everywhere
    ✓ item_at: the listed element whose box is a recorded box (the smallest when a wrapper shares it)
    ✓ same_place: two boxes occupying the same place (enough overlap, or one holds the other's centre)
    ✓ same_inventory_element / shares_data_hook / identity_match: identity by stable attributes; a hook that many
      elements share (every row's menu button) identifies only together with its row
    ✓ could_be_baseline_control: the fail-closed variant: an unidentifiable control counts as an old one
    ✓ compatible / is_interactive: can this action be taken on this control, is it a control at all
    ✓ proof_of: positive proof that the element hit now is not the one recorded earlier (a stable attribute that
      differs, one that now belongs to another element, a different row); missing facts prove nothing
    ✓ describe_control: one line naming a control, every value cut short (the values come from a page)

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import math
import re
from typing import Any, Dict, List, Optional, Tuple

Box = Tuple[float, float, float, float]

BOX_TOLERANCE = 1.0              # px: a recorded action box and the inventory box come from the same page state
MIN_OVERLAP_RATIO = 0.5          # boxes overlapping by this share of their union are in the same place
MAX_FACT_CHARS = 80              # a fact quoted in a reason is cut to this (it comes from a page)
STABLE_KEYS = ("id", "name_attr", "testid", "data_qa", "data_cy")
_INTERACTIVE_TAGS = ("input", "select", "textarea", "button")
_CHECKABLE = {"checkbox", "radio"}
_NON_TEXT_INPUT = {"checkbox", "radio", "button", "submit", "reset", "file", "image", "range", "color"}
_STATE_METHODS_ANY = {"click", "dblclick", "press", "focus", "hover"}

# what identifies a control by its content, and what kind of control it is (could_be_baseline_control)
_IDENTIFYING = ("label", "name", "name_attr", "testid", "placeholder", "title", "text", "alt")
_TEXT_CONTROLS = ("input", "textarea", "select")
KIND_KEYS = ("tag", "role", "type")


def box_of(b: Any) -> Optional[Box]:
    """A page box as (x, y, w, h), or None if it is missing, non-finite, negative or zero-sized (page coordinates
    cannot be negative: such a box is an element parked off screen, not something a person can see)."""
    try:
        v = (float(b["x"]), float(b["y"]), float(b["width"]), float(b["height"]))
    except (TypeError, KeyError, ValueError):
        return None
    if not all(math.isfinite(n) for n in v) or v[0] < 0 or v[1] < 0 or v[2] <= 0 or v[3] <= 0:
        return None
    return v


def norm_text(s: Any) -> str:
    """`s` as comparable text: whitespace collapsed, trimmed, case-folded. The one normaliser of the heal code."""
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def _center(b: Box) -> Tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def _inside(p: Tuple[float, float], b: Box) -> bool:
    return b[0] <= p[0] <= b[0] + b[2] and b[1] <= p[1] <= b[1] + b[3]


def _area(b: Box) -> float:
    return b[2] * b[3]


def overlap(a: Box, b: Box) -> bool:
    """Does either box hold the other's centre?"""
    return _inside(_center(a), b) or _inside(_center(b), a)


def item_at(items: List[Dict[str, Any]], box: Box) -> Optional[Dict[str, Any]]:
    """The listed element whose box is `box` (the smallest when a wrapper shares it)."""
    near = [(i, b) for i in items if (b := box_of(i.get("box")))
            and all(abs(b[k] - box[k]) <= BOX_TOLERANCE for k in range(4))]
    return min(near, key=lambda pair: _area(pair[1]))[0] if near else None


def same_place(a: Box, old: Box) -> bool:
    """Do two page boxes occupy the same place (enough overlap, or one holds the other's centre)?"""
    ix = max(0.0, min(a[0] + a[2], old[0] + old[2]) - max(a[0], old[0]))
    iy = max(0.0, min(a[1] + a[3], old[1] + old[3]) - max(a[1], old[1]))
    inter = ix * iy
    union = a[2] * a[3] + old[2] * old[3] - inter
    return (union > 0 and inter / union >= MIN_OVERLAP_RATIO) or overlap(a, old)


def stable_keys(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    """The attributes that identify an element whatever its copy: id, name, the test hooks, and any other data-* one."""
    extra = sorted({k for k in (*a, *b) if k.startswith("data_")} - set(STABLE_KEYS))
    return [*STABLE_KEYS, *extra]


def shares_data_hook(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Same tag and a data-* attribute (other than the test hooks) with the same value."""
    if (a.get("tag") or "") != (b.get("tag") or ""):
        return False
    return any(a.get(k) and a.get(k) == b.get(k) for k in stable_keys(a, b) if k not in STABLE_KEYS)


def _without_layout(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in entry.items() if k not in ("box", "hidden")}


def same_inventory_element(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Two inventory entries describe the same element: same tag and any of id / name attribute / test id (data-testid,
    data-qa, data-cy) equal, or all attributes identical (the box and hidden flag aside: they change with the
    layout)."""
    if a is b:
        return True
    if (a.get("tag") or "") != (b.get("tag") or ""):
        return False
    if any(a.get(k) and a.get(k) == b.get(k) for k in STABLE_KEYS):
        return True
    return _without_layout(a) == _without_layout(b)


def identity_match(hit: Dict[str, Any], base: Dict[str, Any], items: List[Dict[str, Any]]) -> bool:
    """Is `hit` (a control on the page now) the earlier element `base`, judged by stable attributes? A hook that many
    elements share (every row's menu button is data-cy=row-menu) identifies only together with the row it is in; with no
    row to compare, only a value that occurs on one earlier element does."""
    if not (same_inventory_element(hit, base) or shares_data_hook(hit, base)):
        return False
    shared = [k for k in stable_keys(hit, base) if hit.get(k) and hit.get(k) == base.get(k)]
    if not shared:
        return True                                                    # identical in every attribute but the layout
    if hit.get("row"):
        return hit["row"] == (base.get("row") or "")
    return all(not any(i is not base and i.get("tag") == base.get("tag") and i.get(k) == base.get(k) for i in items)
               for k in shared)


def _identifying(el: Dict[str, Any]) -> Dict[str, str]:
    """Identifying attributes, normalised. A text control's `name` is left out: it follows the copy of the page (an
    aria-label, a typed value on an older probe), not the identity of the element."""
    skip = {"name"} if norm_text(el.get("tag")) in _TEXT_CONTROLS else set()
    return {k: norm_text(el.get(k)) for k in _IDENTIFYING if k not in skip}


def could_be_baseline_control(hit: Dict[str, Any], base: Dict[str, Any]) -> bool:
    """Could `hit` (a control on the page now) be `base` (one on the page when the test passed)? Fails closed: the same
    id, or the same tag + role + type and any one identifying attribute, normalised; a hit with nothing to identify it
    counts as the same. (same_inventory_element is the stricter test between two entries of one inventory.)"""
    if norm_text(hit.get("id")) and norm_text(hit.get("id")) == norm_text(base.get("id")):
        return True
    hit_ids, base_ids = _identifying(hit), _identifying(base)
    if not any(hit_ids.values()):
        return True
    if any(norm_text(hit.get(k)) != norm_text(base.get(k)) for k in KIND_KEYS):
        return False
    return any(v and v == base_ids.get(k) for k, v in hit_ids.items())


def is_interactive(c: Dict[str, Any]) -> bool:
    """A control a person can act on: a form control, a button, or anything with an ARIA role."""
    return norm_text(c.get("tag")) in _INTERACTIVE_TAGS or bool(c.get("role"))


def compatible(method: str, c: Dict[str, Any]) -> bool:
    """Can the action `method` (a Playwright method name, snake_case) be taken on control `c`? Unknown actions are
    allowed: only the widget-specific ones (select, check, fill, upload) are checked."""
    tag, typ, role = norm_text(c.get("tag")), norm_text(c.get("type")), norm_text(c.get("role"))
    if method == "select_option":
        return tag == "select" or role in ("combobox", "listbox")
    if method in ("check", "uncheck", "set_checked"):
        return (tag == "input" and typ in _CHECKABLE) or role in ("checkbox", "radio", "switch")
    if method in ("fill", "type", "clear"):
        return (tag == "textarea" or (tag == "input" and typ not in _NON_TEXT_INPUT)
                or role in ("textbox", "searchbox", "combobox", "spinbutton"))
    if method == "set_input_files":
        return tag == "input" and typ == "file"
    return True


def describe_control(c: Dict[str, Any]) -> str:
    """One line naming a control: tag, type, the identifying attributes it has, flags and box."""
    bits = [str(c.get("tag") or "?")[:MAX_FACT_CHARS]]
    if c.get("type"):
        bits.append(f"type={str(c['type'])[:MAX_FACT_CHARS]}")
    for k in ("id", "name", "label", "role", "testid", "placeholder", "text"):
        if c.get(k):
            bits.append(f'{k}="{str(c[k])[:50]}"')
    if c.get("disabled"):
        bits.append("disabled")
    b = box_of(c.get("box"))
    if b:
        bits.append("box=(%d,%d,%d,%d)" % tuple(round(v) for v in b))
    return " ".join(bits)


# ---- proof that the element hit now is not the one recorded -----------------------------------------------------

def _short(v: Any) -> str:
    return str(v)[:MAX_FACT_CHARS]


def _key_name(key: str) -> str:
    """The attribute a fact key stands for (`data_qa` -> `data-qa`), bounded: the key comes from the page."""
    return key.replace("_", "-")[:MAX_FACT_CHARS]


def _holder_elsewhere(key: str, value: str, items: List[Dict[str, Any]], hit_box: Any) -> Optional[Dict[str, Any]]:
    """The one listed element carrying `key=value`, when it is not the element hit now; None otherwise."""
    holders = [i for i in items if i.get(key) == value]
    if len(holders) != 1:
        return None
    hb, ib = box_of(hit_box), box_of(holders[0].get("box"))
    return None if hb is not None and ib is not None and same_place(ib, hb) else holders[0]


def proof_of(then: Dict[str, Any], now: Dict[str, Any], now_items: List[Dict[str, Any]],
             now_box: Any) -> Optional[Tuple[str, str]]:
    """(proof, why) that `now` (the facts of the element hit now) is not `then` (the recorded element's), or None.
    Only a stable attribute that differs, one that now belongs to exactly one other element, or a different row proves
    it; a changed label proves nothing."""
    keys = [k for k in stable_keys(then, now) if then.get(k)]
    for k in keys:
        if now.get(k) and now[k] != then[k]:
            return "identity", f"its {_key_name(k)} was {_short(then[k])!r} and is now {_short(now[k])!r}"
    for k in keys:
        if now.get(k) != then[k] and _holder_elsewhere(k, then[k], now_items, now_box) is not None:
            return "moved", f"{_key_name(k)}={_short(then[k])!r} now belongs to another element on the page"
    if then.get("row") and now.get("row") and then["row"] != now["row"]:
        return "row", f"it was in {_short(then['row'])!r} and is now in {_short(now['row'])!r}"
    return None


# The original names, kept as aliases for the helpers that were module-private there.
_same_place, _stable_keys, _shares_data_hook = same_place, stable_keys, shares_data_hook
_compatible, _is_interactive, _KIND_KEYS = compatible, is_interactive, KIND_KEYS
