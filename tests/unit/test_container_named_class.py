"""
===============================================================================
Container scope with a class the broken call named (a library class the generated-id filter would reject)
===============================================================================
`data-test=category-space-permission >> .MuiSelect-select` broke because the container's hook attribute changed. The select
inside it still has the class the call named, but `is_generated_id` reads the `mui` prefix of `MuiSelect-select` as a
generated id, so the writer had no inner locator and the heal ended no_stable_locator. A class the broken call itself names,
with no digit in it, is allowed as an inner locator INSIDE a hooked container, where it must still be unique among the
container's elements. Everywhere else the filter is unchanged. The whole path is opt in: HEAL_LIBRARY_CLASSES must list the class's library (mui here); with
it unset no class is written. (The select's explicit role attribute is, since the row scope also writes `div[role=combobox]`
where it is unique in the container, see test_scoped_forms.py: these tests are about the class, so they check that none is
written, and the container carries a second combobox where they need no candidate at all.)

Author: PMAC
===============================================================================
"""
import pytest

from voyageur.inventory import Inventory
from voyageur.stages import write_for

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def library_classes_on(monkeypatch):
    """The path is opt in (HEAL_LIBRARY_CLASSES): these tests run with Mui listed; the off case is the last test."""
    monkeypatch.setenv("HEAL_LIBRARY_CLASSES", "Mui")

CALL = 'page.locator("data-test=category-space-permission >> .MuiSelect-select")'


def box(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def page(target_class="MuiSelect-select MuiSelect-outlined", second_in_container=False, drop_row=False):
    items = [
        {"ref": "c1", "tag": "div", "testid": "category-space-permission", "box": box(0, 0, 400, 40)},
        {"ref": "t", "tag": "div", "role": "combobox", "class": target_class, "box": box(5, 5, 300, 30),
         **({} if drop_row else {"row": "category-space-permission", "row_attr": "data-testid"})},
        {"ref": "c2", "tag": "div", "testid": "role-picker", "box": box(0, 100, 400, 40)},
        {"ref": "o", "tag": "div", "role": "combobox", "class": "MuiSelect-select MuiSelect-outlined", "box": box(5, 105, 300, 30),
         "row": "role-picker", "row_attr": "data-testid"},
    ]
    if second_in_container:
        items.append({"ref": "t2", "tag": "div", "role": "combobox", "class": "MuiSelect-select", "box": box(310, 5, 80, 30),
                      "row": "category-space-permission", "row_attr": "data-testid"})
    return items


def run(items, call=CALL, target="t", language="python"):
    return write_for(Inventory(items, True), target, language, "page", "chain", "data-testid", call)


def test_the_class_the_call_named_is_written_inside_the_hooked_container():
    res = run(page())
    assert res.candidates, res.reason
    best = res.candidates[0]
    assert best.code["python"] == ("page.locator(\"[data-testid='category-space-permission']\")"
                                   ".locator(\"div[role='combobox'].MuiSelect-select\")")
    assert best.code["typescript"] == ("page.locator('[data-testid=\"category-space-permission\"]')"
                                       ".locator('div[role=\"combobox\"].MuiSelect-select')")
    assert best.kind == "css" and not best.copy_based


def test_only_the_classes_the_call_named_are_used():
    best = run(page()).candidates[0]
    assert "MuiSelect-outlined" not in best.code["python"]


def classes_written(res):
    return [w for w in res.candidates if ".Mui" in w.code["python"] or ".css-" in w.code["python"] or ".other" in w.code["python"]]


def test_a_class_the_call_does_not_name_is_still_rejected_as_generated():
    res = run(page(), call='page.locator("data-test=category-space-permission >> .other")')
    assert not classes_written(res)


def test_a_class_with_a_digit_is_never_taken_from_the_call():
    items = page(target_class="css-1xc3v61-indicatorContainer")
    res = run(items, call='page.locator("[data-test=category-space-permission] .css-1xc3v61-indicatorContainer")')
    assert not classes_written(res)


def test_the_class_must_be_unique_inside_the_container():
    assert not run(page(second_in_container=True)).candidates


def test_a_target_without_an_explicit_role_gets_nothing_a_bare_class_cannot_be_counted():
    items = page()
    del items[1]["role"]
    assert not run(items).candidates


def test_the_class_must_be_on_the_target():
    assert not classes_written(run(page(target_class="Other-thing")))


def test_no_container_no_class_from_the_call():
    res = run(page(drop_row=True))
    assert not res.candidates                         # nothing unique on the page, and no hooked container to scope to


def test_the_setting_off_the_class_is_not_written_and_with_a_twin_nothing_is(monkeypatch):
    monkeypatch.delenv("HEAL_LIBRARY_CLASSES", raising=False)
    assert not classes_written(run(page()))
    res = run(page(second_in_container=True))
    assert not res.candidates and res.reason == "no_stable_locator"


def test_a_library_that_is_not_listed_does_not_open_the_path(monkeypatch):
    monkeypatch.setenv("HEAL_LIBRARY_CLASSES", "ant")
    assert not classes_written(run(page()))
    assert not run(page(second_in_container=True)).candidates


def test_a_hashed_class_the_call_names_is_refused_even_with_the_library_listed():
    items = page(target_class="MuiSelect-select-a1b2c3d4")
    assert not classes_written(run(items, call='page.locator("[data-test=category-space-permission] .MuiSelect-select-a1b2c3d4")'))


def test_at_page_level_the_role_form_is_written_for_a_named_library_class_that_is_unique(monkeypatch):
    items = [{"ref": "t", "tag": "div", "role": "combobox", "class": "MuiSelect-select", "box": box(5, 5, 300, 30)}]
    res = write_for(Inventory(items, True), "t", "python", "page", "chain", "data-testid", 'page.locator(".MuiSelect-select")')
    assert [w.code["python"] for w in res.candidates] == ['page.locator("div[role=\'combobox\'].MuiSelect-select")']
    monkeypatch.delenv("HEAL_LIBRARY_CLASSES")
    assert not write_for(Inventory(items, True), "t", "python", "page", "chain", "data-testid", 'page.locator(".MuiSelect-select")').candidates
