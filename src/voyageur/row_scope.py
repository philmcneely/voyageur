"""
===============================================================================
Row Scope (a locator scoped to the hooked row an element sits in, when nothing on the page is unique)
===============================================================================
A table of orders has a status badge in every row, each with data-qa=status. None of the badge's own locators is unique
on the page, so the writer has nothing to offer and the heal would end needs_human, although the test names one row
(`[data-qa='order-row-1042'] [data-qa='status']`). The probe lists, for every element, the nearest ancestor with a test
hook (`row`) and which attribute it came from (`row_attr`). This module turns that into a scope: the row's own hook
selector, and the elements that sit inside it, so the writer can look for a locator that is unique inside the row and
put the row in front of it: page.locator('[data-qa="order-row-1042"]').locator('[data-qa=status]').

The scope is never a guess at structure: it is a hook attribute with its exact value, and the inner locator is one of the
writer's own (test id, id, name, css, role, label, placeholder, text), proven unique among the row's elements.

Features:
    ✓ row_scope(): (prefix, prefix in the other language, Inventory of the row's elements) or None
    ✓ the row's elements are those whose nearest hook is the row, plus every listed element inside the row's box when the
      row itself is listed (so a nested hooked group cannot hide a second match)
    ✓ the probe lists every element with a hook, so the row must be listed exactly once, with a box; else no scope
    ✓ nested hooked groups inside the row are followed, so a second match hidden in one blocks the scope
    ✓ no scope when the hook may have been cut short by the probe (80 UTF-16 units), holds whitespace, is a generated id
      or a run-to-run value (a 6+ digit run, a hash-like token); a leading or trailing space the probe trimmed is not seen
      here, and the live check (count 1, the chosen element) refuses such a locator
    ✓ inside a row only the kinds counted from the list are written (no text or label: their count needs the snapshot)
    ✓ a probe without row_attr: the attribute is taken from the one listed element that holds the row value as its first
      hook
    ✓ a row whose hook cannot be written (a generated id: the listbox of a react-select widget) is climbed past: the element
      holding that hook is listed exactly once and is an ancestor of the target, so its own row is an ancestor of the target
      too (at most ROW_DEPTH hops); a hook that is not listed exactly once, or a holder with no row, ends the climb with no scope
    ✓ inside a row the writer may also use an explicit role attribute (`div[role="combobox"]`) and the stable tail of a
      generated id (`[id$="-option-6"]`), which only mean something inside one container

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
from typing import Any, Dict, List, Optional, Tuple

from .elements import box_of
from .inventory import Inventory
from .locator_policy import _attr_value, _brittle, _id_selector, _py, _swap_receiver, _ts, is_generated_id

HOOKS = (("data-qa", "data_qa"), ("data-testid", "testid"), ("data-test", "data_test"), ("data-cy", "data_cy"),
         ("id", "id"))                               # the probe's order: the first one set is the element's hook
_KEY = dict(HOOKS)
ROW_CHARS = 80                                       # the probe cuts a row value at this length (UTF-16 units)
ROW_DEPTH = 8                                        # nested hooked groups followed inside a row
# no text / label: their count needs the snapshot. role_attr and id_part are written only here (see locator_policy.candidates)
SCOPED_ORDER = ("testid", "id", "name", "css", "role_attr", "id_part", "role", "placeholder")


def _first_hook(e: Dict[str, Any]) -> Tuple[str, str]:
    for attr, key in HOOKS:
        v = e.get(key)
        if isinstance(v, str) and v:
            return v, attr
    return "", ""


def _inside(inner: Any, outer: Any) -> bool:
    return (inner[0] >= outer[0] and inner[1] >= outer[1] and inner[0] + inner[2] <= outer[0] + outer[2]
            and inner[1] + inner[3] <= outer[1] + outer[3])


def _selector(attr: str, value: str, py: bool) -> str:
    return _id_selector(value, py) if attr == "id" else f"[{attr}={_attr_value(value, py)}]"


def _usable(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and len(value.encode("utf-16-le")) < 2 * ROW_CHARS \
        and not any(c.isspace() for c in value) and not _brittle(value)


def _holders(inv: Inventory, value: str, attr: str) -> Tuple[List[Dict[str, Any]], str]:
    """The listed elements holding row hook `value` and the attribute it is (from the probe's `row_attr`, else the first hook
    of the one element that holds it)."""
    if attr:
        return [e for e in inv.elements if e.get(_KEY.get(attr, "")) == value], attr
    holders = [e for e in inv.elements if _first_hook(e)[0] == value]
    return holders, (_first_hook(holders[0])[1] if len(holders) == 1 else "")


def row_scope(target: Dict[str, Any], inv: Inventory, language: str,
              receiver: str) -> Optional[Tuple[str, str, Inventory]]:
    """The row scope of `target` (an element of `inv`), or None when it has none that can be written. A row hook that cannot be
    written (a generated id) is climbed past to the row of the one element that holds it."""
    value, attr = target.get("row") or "", target.get("row_attr") or ""
    holders, attr = _holders(inv, value, attr) if isinstance(value, str) and value else ([], "")
    for _ in range(ROW_DEPTH):
        if not (isinstance(value, str) and value) or (_usable(value) and not (attr == "id" and is_generated_id(value))):
            break
        # the hook is no use as a scope, but its holder is an ancestor of the target: the holder's own row is one too
        if len(holders) != 1 or holders[0] is target or not holders[0].get("row"):
            return None
        value, attr = holders[0]["row"], holders[0].get("row_attr") or ""
        holders, attr = _holders(inv, value, attr)
    if not _usable(value):
        return None
    # the probe lists every element with a hook, so the row is listed exactly once; anything else is not one row
    if len(holders) != 1 or holders[0] is target or not attr or (attr == "id" and is_generated_id(value)):
        return None
    holder = holders[0]
    outer = box_of(holder.get("box"))
    if outer is None:                                    # no box to find the row's other descendants by
        return None
    members = _members(inv.elements, holder, value, outer)
    if not any(e is target for e in members):
        return None
    sub = Inventory(members, inv.complete, "")
    sub.inherit(inv)
    py = language == "python"
    other = "typescript" if py else "python"
    q, q_other = (_py, _ts) if py else (_ts, _py)
    prefix = f"{receiver}.locator({q(_selector(attr, value, py))})"
    prefix_other = f"{_swap_receiver(receiver, other)}.locator({q_other(_selector(attr, value, not py))})"
    return prefix, prefix_other, sub


def _members(elements: List[Dict[str, Any]], holder: Dict[str, Any], value: str, outer: Any) -> List[Dict[str, Any]]:
    """The listed elements inside the row: nearest hook the row, or inside its box, or (followed to a fixed point) nearest
    hook a group already found inside the row, so a nested group's hidden or overflowing element is not missed."""
    rows = {value}
    picked: Dict[int, Dict[str, Any]] = {}
    for _ in range(ROW_DEPTH):
        grew = False
        for e in elements:
            if e is holder or id(e) in picked:
                continue
            b = box_of(e.get("box"))
            if e.get("row") in rows or (b is not None and _inside(b, outer)):
                picked[id(e)] = e
                grew = True
                hook = _first_hook(e)[0]
                if hook:
                    rows.add(hook)
        if not grew:
            break
    return list(picked.values())
