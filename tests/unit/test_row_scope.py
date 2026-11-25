"""
===============================================================================
Row scope tests (no locator of the element is unique on the page: scope it to the hooked row it sits in)
===============================================================================
Modelled on the orders testbed: every row has a status badge with data-qa=status, the step names one row. Before, the
writer had nothing unique and the heal ended needs_human (no_stable_locator); now it writes the row's hook in front of a
locator unique inside the row. Plus every case where no scope may be written.

Author: PMAC
===============================================================================
"""
import pytest

from voyageur.inventory import Inventory
from voyageur.row_scope import row_scope
from voyageur.stages import write_for

pytestmark = pytest.mark.unit

CALL = "page.locator(\"[data-qa='order-row-1042'] [data-qa='status']\")"


def box(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def orders(row_attr="data-qa", listed_rows=True, extra=()):
    out = []
    for i, n in enumerate((1040, 1041, 1042)):
        y = 100 + 40 * i
        if listed_rows:
            out.append({"ref": f"r{n}", "tag": "tr", "data_qa": f"order-row-{n}", "box": box(0, y, 600, 40)})
        out.append({"ref": f"s{n}", "tag": "button", "type": "button", "data_qa": "status", "text": "paid", "name": "paid",
                    "row": f"order-row-{n}", "row_attr": row_attr, "box": box(300, y + 5, 60, 30)})
    return out + list(extra)


def test_a_badge_in_every_row_gets_the_row_in_front_of_it():
    inv = Inventory(orders(), True)
    res = write_for(inv, "s1042", "python", "page", "chain", "data-testid", CALL)
    assert res.candidates, res.reason
    best = res.candidates[0]
    assert best.code["python"] == "page.locator(\"[data-qa='order-row-1042']\").locator(\"[data-qa='status']\")"
    assert best.code["typescript"] == "page.locator('[data-qa=\"order-row-1042\"]').locator('[data-qa=\"status\"]')"


def test_typescript_is_written_in_its_own_quoting():
    inv = Inventory(orders(), True)
    res = write_for(inv, "s1042", "typescript", "page", "chain", "data-testid", CALL.replace('"', "'", 0))
    assert res.candidates[0].code["typescript"].startswith("page.locator('[data-qa=\"order-row-1042\"]').locator(")


def test_an_element_unique_on_the_page_is_not_scoped():
    items = orders() + [{"ref": "x", "tag": "button", "data_qa": "export", "text": "Export", "row": "order-row-1042",
                         "row_attr": "data-qa", "box": box(400, 185, 60, 30)}]
    res = write_for(Inventory(items, True), "x", "python", "page", "chain", "data-testid", "page.locator('#export')")
    assert "order-row" not in res.candidates[0].code["python"]


def test_no_scope_when_two_listed_elements_hold_the_row_hook():
    items = orders() + [{"ref": "dup", "tag": "div", "data_qa": "order-row-1042", "box": box(0, 500, 600, 40)}]
    assert row_scope(next(e for e in items if e["ref"] == "s1042"), Inventory(items, True), "python", "page") is None


def test_no_scope_for_a_generated_id_a_cut_value_or_whitespace():
    for value, attr in (("ember1234", "id"), ("x" * 80, "data-qa"), ("order row", "data-qa")):
        items = [{"ref": "s", "tag": "button", "data_qa": "status", "row": value, "row_attr": attr, "box": box(0, 0, 9, 9)},
                 {"ref": "t", "tag": "button", "data_qa": "status", "row": value, "row_attr": attr, "box": box(0, 20, 9, 9)}]
        inv = Inventory(items, True)
        assert row_scope(inv.elements[0], inv, "python", "page") is None, value


def test_a_second_match_in_a_nested_hooked_group_inside_the_row_blocks_the_scope():
    # the row holds a nested group with its own hook: its badge's nearest hook is the group, not the row, but
    # [data-qa=order-row-1042] [data-qa=status] would match it too; the row's box finds it
    nested = {"ref": "n", "tag": "button", "data_qa": "status", "text": "paid", "row": "refund-group", "row_attr": "data-qa",
              "box": box(400, 185, 60, 30)}
    res = write_for(Inventory(orders(extra=[nested]), True), "s1042", "python", "page", "chain", "data-testid", CALL)
    assert not any("[data-qa='status']\")" in w.code["python"] and "order-row" in w.code["python"] for w in res.candidates)


def test_an_older_probe_without_row_attr_uses_the_one_listed_row_that_holds_the_value():
    inv = Inventory(orders(row_attr=""), True)
    res = write_for(inv, "s1042", "python", "page", "chain", "data-testid", CALL)
    assert res.candidates[0].code["python"].startswith("page.locator(\"[data-qa='order-row-1042']\")")
    inv = Inventory(orders(row_attr="", listed_rows=False), True)
    assert write_for(inv, "s1042", "python", "page", "chain", "data-testid", CALL).candidates == []


def test_no_scope_when_the_row_is_not_listed_or_has_no_box():
    # the probe lists every element with a hook: an unlisted row means the list cannot speak for it
    inv = Inventory(orders(listed_rows=False), True)
    assert write_for(inv, "s1041", "python", "page", "chain", "data-testid", CALL).candidates == []
    items = orders()
    next(e for e in items if e["ref"] == "r1041").pop("box")
    inv = Inventory(items, True)
    assert row_scope(next(e for e in inv.elements if e["ref"] == "s1041"), inv, "python", "page") is None


def test_a_hidden_second_match_in_a_nested_group_blocks_the_scope():
    # the nested group is inside the row's box; its badge is hidden (no box), found through the group's hook
    group = {"ref": "g", "tag": "div", "data_qa": "refunds", "row": "order-row-1042", "row_attr": "data-qa",
             "box": box(350, 182, 200, 36)}
    hidden = {"ref": "h", "tag": "button", "data_qa": "status", "text": "paid", "row": "refunds", "row_attr": "data-qa",
              "hidden": True}
    inv = Inventory(orders(extra=[group, hidden]), True)
    _, _, sub = row_scope(next(e for e in inv.elements if e["ref"] == "s1042"), inv, "python", "page")
    assert {e["ref"] for e in sub.elements} >= {"s1042", "g", "h"}
    res = write_for(inv, "s1042", "python", "page", "chain", "data-testid", CALL)
    assert not any("order-row" in w.code["python"] and "[data-qa='status']\")" in w.code["python"] for w in res.candidates)


def test_no_scope_for_a_run_to_run_hook_value():
    for value in ("toast-1699999999", "card-9f8e7d6c5b", "order-row-1042"):
        items = [{"ref": "r", "tag": "div", "data_qa": value, "box": box(0, 0, 300, 100)},
                 {"ref": "s", "tag": "button", "data_qa": "status", "row": value, "row_attr": "data-qa", "box": box(5, 5, 9, 9)}]
        inv = Inventory(items, True)
        got = row_scope(inv.elements[1], inv, "python", "page")
        assert (got is not None) == (value == "order-row-1042"), value


def test_text_is_never_written_inside_a_row():
    items = orders()
    for e in items:
        if e["ref"].startswith("s"):
            del e["data_qa"]                          # only the copy is left to tell the badge
    res = write_for(Inventory(items, True), "s1042", "python", "page", "chain", "data-testid", CALL)
    assert not any("get_by_text" in w.code["python"] for w in res.candidates)


def test_a_scoped_candidate_says_it_is_unique_inside_its_row():
    res = write_for(Inventory(orders(), True), "s1042", "python", "page", "chain", "data-testid", CALL)
    assert res.candidates[0].scope.startswith("locator(") and "order-row-1042" in res.candidates[0].scope


def test_an_id_row_is_written_as_an_id_selector():
    items = [{"ref": "p", "tag": "section", "id": "billing", "box": box(0, 0, 500, 300)},
             {"ref": "a", "tag": "button", "data_qa": "save", "text": "Save", "row": "billing", "row_attr": "id",
              "box": box(10, 10, 60, 30)},
             {"ref": "b", "tag": "button", "data_qa": "save", "text": "Save", "row": "shipping", "row_attr": "id",
              "box": box(10, 400, 60, 30)}]
    res = write_for(Inventory(items, True), "a", "python", "page", "chain", "data-testid", "page.locator('#billing .save')")
    assert res.candidates[0].code["python"] == "page.locator(\"#billing\").locator(\"[data-qa='save']\")"
