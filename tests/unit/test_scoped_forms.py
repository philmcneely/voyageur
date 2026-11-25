"""
===============================================================================
Scoped forms (what the writer can say inside a hooked container when the element's own locators are not unique or stable)
===============================================================================
Two elements had nothing the writer would write, although each sat in a container with a stable test id:

    a select's combobox   `<div role="combobox" class="MuiSelect-select ...">` in `data-testid=category-space-permission`: no id,
                          name or copy, and the library class is not written (HEAL_LIBRARY_CLASSES is off). The explicit
                          role attribute is a semantic, non-copy attribute the listing counts (the probe lists every element
                          with a role): `div[role="combobox"]`, unique in the container.
    a react-select option `id="react-select-4-option-6"` in a listbox with a generated id, in `data-testid=city-select`: the id
                          is generated (a counter), its tail `-option-6` is not. The row is the listbox (a generated id), so
                          the scope climbs to the listbox's own container, and the tail is written as `[id$="-option-6"]`.

Both are written only inside a scope, never page-wide: the tail of a generated id or a role says little about a whole page, and a
container's hook is what makes it one widget's. (The options here carry no copy, as the bench page's aria snapshot leaves them: a
page-wide `get_by_text` the listing can count is written first, as it always was.)

Author: PMAC
===============================================================================
"""
import pytest

from voyageur.inventory import Inventory
from voyageur.locator_policy import _stable_tail, kind_of
from voyageur.row_scope import row_scope
from voyageur.stages import write_for

pytestmark = pytest.mark.unit


def box(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def run(items, ref, call, language="python"):
    return write_for(Inventory(items, True), ref, language, "page", "chain", "data-testid", call)


# ---- an explicit role attribute inside a hooked container ------------------------------------------------------------
SELECT_CALL = 'page.locator("data-test=category-space-permission >> .MuiSelect-select")'


def selects(extra=()):
    return [
        {"ref": "c1", "tag": "div", "testid": "category-space-permission", "box": box(0, 0, 400, 40)},
        {"ref": "t", "tag": "div", "role": "combobox", "class": "MuiSelect-select MuiSelect-outlined",
         "row": "category-space-permission", "row_attr": "data-testid", "box": box(5, 5, 300, 30)},
        {"ref": "c2", "tag": "div", "testid": "role-picker", "box": box(0, 100, 400, 40)},
        {"ref": "o", "tag": "div", "role": "combobox", "class": "MuiSelect-select", "row": "role-picker",
         "row_attr": "data-testid", "box": box(5, 105, 300, 30)},
        *extra,
    ]


def test_an_explicit_role_unique_in_the_container_is_written_with_the_library_classes_off(monkeypatch):
    monkeypatch.delenv("HEAL_LIBRARY_CLASSES", raising=False)
    res = run(selects(), "t", SELECT_CALL)
    assert res.candidates, res.reason
    best = res.candidates[0]
    assert best.code["python"] == ("page.locator(\"[data-testid='category-space-permission']\")"
                                   ".locator(\"div[role='combobox']\")")
    assert best.code["typescript"] == ("page.locator('[data-testid=\"category-space-permission\"]')"
                                       ".locator('div[role=\"combobox\"]')")
    assert best.kind == "css" and not best.copy_based
    assert not any("MuiSelect" in w.code["python"] for w in res.candidates)         # the library class is still not written


def test_a_second_element_with_that_role_in_the_container_blocks_it(monkeypatch):
    monkeypatch.delenv("HEAL_LIBRARY_CLASSES", raising=False)
    twin = {"ref": "t2", "tag": "div", "role": "combobox", "row": "category-space-permission", "row_attr": "data-testid",
            "box": box(310, 5, 80, 30)}
    assert not run(selects([twin]), "t", SELECT_CALL).candidates


def test_the_role_is_not_written_page_wide(monkeypatch):
    monkeypatch.delenv("HEAL_LIBRARY_CLASSES", raising=False)
    only = [e for e in selects() if e["ref"] in ("c2", "o")]                        # an element in no container of its own
    only[1].pop("row")
    only[1].pop("row_attr")
    assert not run(only, "o", SELECT_CALL).candidates


def test_an_implied_role_is_not_an_attribute_to_write(monkeypatch):
    monkeypatch.delenv("HEAL_LIBRARY_CLASSES", raising=False)
    items = selects()
    items[1].pop("role")
    items[1]["tag"] = "select"                                                      # combobox by its tag, no role attribute
    assert not any("role" in w.code["python"] for w in run(items, "t", SELECT_CALL).candidates)


# ---- the tail of a generated id --------------------------------------------------------------------------------------
OPTION_CALL = 'page.locator("#react-select-3-option-6")'


def city(n_extra_listbox=False):
    items = [
        {"ref": "e4", "tag": "div", "id": "react-select-3-placeholder", "text": "Select region", "row": "region-select",
         "row_attr": "data-testid", "box": box(23, 125, 1234, 20)},
        {"ref": "e5", "tag": "input", "type": "text", "role": "combobox", "id": "react-select-3-input", "row": "region-select",
         "row_attr": "data-testid", "box": box(23, 145, 164, 30)},
        {"ref": "e6", "tag": "div", "testid": "city-select", "text": "City 0 City 1", "box": box(23, 216, 1234, 206)},
        {"ref": "e7", "tag": "input", "type": "text", "role": "combobox", "id": "react-select-4-input", "row": "city-select",
         "row_attr": "data-testid", "box": box(23, 216, 164, 30)},
        {"ref": "e8", "tag": "div", "id": "react-select-4-listbox", "row": "city-select", "row_attr": "data-testid",
         "box": box(23, 245, 1234, 176)},
    ]
    for i in range(9):
        items.append({"ref": f"o{i}", "tag": "div", "id": f"react-select-4-option-{i}", "row": "react-select-4-listbox",
                      "row_attr": "id", "class": "css-10wo9uf-option", "box": box(23, 245 + 19.6 * i, 1234, 19.6)})
    return items


def test_the_tail_of_a_generated_id_is_written_inside_the_containers_scope():
    res = run(city(), "o6", OPTION_CALL)
    assert res.candidates, res.reason
    best = res.candidates[0]
    assert best.code["python"] == "page.locator(\"[data-testid='city-select']\").locator(\"[id$='-option-6']\")"
    assert best.code["typescript"] == "page.locator('[data-testid=\"city-select\"]').locator('[id$=\"-option-6\"]')"
    assert best.kind == "css" and not best.copy_based


def test_the_scope_climbs_past_a_generated_id_to_the_next_hooked_container():
    inv = Inventory(city(), True)
    prefix, _, sub = row_scope(next(e for e in inv.elements if e["ref"] == "o6"), inv, "python", "page")
    assert prefix == "page.locator(\"[data-testid='city-select']\")"
    assert {"o6", "e8", "e7"} <= {e["ref"] for e in sub.elements}


def test_no_climb_when_the_generated_id_is_not_listed_exactly_once():
    items = [e for e in city() if e["ref"] != "e8"]                                 # the listbox is not listed
    inv = Inventory(items, True)
    assert row_scope(next(e for e in inv.elements if e["ref"] == "o6"), inv, "python", "page") is None
    twin = dict(next(e for e in city() if e["ref"] == "e8"), ref="e8b")
    inv = Inventory(city() + [twin], True)
    assert row_scope(next(e for e in inv.elements if e["ref"] == "o6"), inv, "python", "page") is None


def test_a_second_element_with_the_same_tail_in_the_container_blocks_it():
    dup = {"ref": "x", "tag": "div", "id": "react-select-9-option-6", "row": "city-select", "row_attr": "data-testid",
           "box": box(23, 430, 100, 20)}
    assert not run(city() + [dup], "o6", OPTION_CALL).candidates


def test_the_tail_is_not_written_page_wide():
    items = [{"ref": "a", "tag": "div", "id": "react-select-4-option-6", "box": box(0, 0, 50, 20)},
             {"ref": "b", "tag": "div", "text": "x", "testid": "other", "box": box(0, 40, 50, 20)}]
    assert not run(items, "a", OPTION_CALL).candidates


def test_stable_tail_of_generated_ids():
    assert _stable_tail("react-select-4-option-6") == "-option-6"
    assert _stable_tail("react-select-4-input") == "-input"
    assert _stable_tail(":Rajsq:-form-item") == "-form-item"
    assert _stable_tail("mui-123-radio-group-2-label") == "-label"
    for hopeless in ("react-select-4", "headlessui-menu-button-:r5:", "ember1234", "row-1699999999", "x-4-ab", "a b-4-option",
                     "react-select-4-opt'ion", "react-select-4-" + "a" * 185):         # the last may be an id the probe cut short
        assert _stable_tail(hopeless) is None, hopeless


def test_attribute_operator_selectors_have_the_tier_of_a_css_locator():
    assert kind_of('page.locator("[id$=\'-option-6\']")') == "css"
    assert kind_of('page.locator("div[role=\'combobox\']")') == "css"
    assert kind_of('page.locator("[id$=\'-a\'] [id$=\'-b\']")') == "chain"
