"""Gates: a pick must pass before a locator is written; a client's result must prove the heal before it counts."""
import pytest

from voyageur.failing import is_missing_locator
from voyageur.gates import judge_result, own_check_line, verify_pick
from voyageur.schemas import ResultRequest, StackFrame

pytestmark = pytest.mark.unit


def el(ref, **kw):
    return {"ref": ref, "tag": "input", "type": "text", **kw}


ITEMS = [el("e0", id="user"), el("e1", id="other")]


def test_a_pick_not_on_the_list_is_refused():
    assert verify_pick(ITEMS[0], {"e1"}, ITEMS, None, None) == "not_on_list"


def test_a_hidden_pick_or_one_the_action_cannot_use_is_refused():
    assert verify_pick(el("e0", hidden=True), {"e0"}, [], None, None) == "not_visible"
    assert verify_pick({"ref": "e0", "tag": "div"}, {"e0"}, [], None, "fill") == "action_incompatible"


def test_a_pick_is_refused_when_the_old_element_still_exists_elsewhere():
    fp = {"tag": "input", "id": "user"}
    assert verify_pick(ITEMS[1], {"e0", "e1"}, ITEMS, fp, "fill") == "different_element"


def test_a_renamed_attribute_on_the_picked_element_is_not_a_reason_to_refuse():
    fp = {"tag": "input", "id": "username", "box": ITEMS[0].get("box") or {"x": 5, "y": 5, "width": 90, "height": 20}}
    items = [{**ITEMS[0], "box": fp["box"]}, ITEMS[1]]  # the id changed (the drift) and the element is where it was
    assert verify_pick(items[0], {"e0", "e1"}, items, fp, "fill") is None


def test_a_different_row_is_refused():
    items = [{"ref": "e0", "tag": "button", "row": "r2"}]
    assert verify_pick(items[0], {"e0"}, items, {"tag": "button", "row": "r1"}, None) == "different_element"


@pytest.mark.parametrize("body, verdict", [
    ({"count": 1, "hit": {"ref": "e7"}, "test_outcome": "passed"}, "ok"),
    ({"count": 0}, "count"), ({"count": 2, "hit": {"ref": "e7"}, "test_outcome": "passed"}, "count"), ({}, "count"),
    ({"count": 1, "test_outcome": "passed"}, "unverified"),
    ({"count": 1, "hit": {"ref": "e9"}, "test_outcome": "passed"}, "wrong_element"),
    ({"count": 1, "hit": {"ref": "e7"}, "test_outcome": "failed"}, "rerun_failed"),
    ({"count": 1, "hit": {"ref": "e7"}}, "rerun_failed"),
    ({"count": 1, "hit": {"ref": "e7"}, "test_outcome": "error"}, "rerun_failed"),
])
def test_only_count_one_the_right_element_and_a_passing_exact_test_is_ok(body, verdict):
    assert judge_result("e7", ResultRequest.model_validate(body)) == verdict


# the healed line passed and the test now stops LATER: at a different locator that found nothing (the next round can heal it), or
# anywhere past the healed statement's own check. "Later" is proven by the stacks: the re-run's frames, outermost first, reach a
# later line than the original failure's. A failure that is not later, or not provably so, keeps nothing.
HEALED = 'self.page.get_by_test_id("login-user")'
MISSING = "locator.click: Timeout 5000ms exceeded.\nCall log:\n  - waiting for locator('#save')"
ORIG = [{"file": "pages/login_page.py", "line": 6}, {"file": "tests/test_login.py", "line": 4}]     # innermost first
AT = ("pages/login_page.py", 6)


def later(error, stack=None, file=None, line=None):
    stack = stack if stack is not None else [{"file": "pages/login_page.py", "line": 12}, {"file": "tests/test_login.py", "line": 4}]
    top = stack[0] if stack else {}
    return ResultRequest.model_validate({"count": 1, "hit": {"ref": "e7"}, "test_outcome": "failed_later", "later": {
        "error": error, "file": file or top.get("file"), "line": line or top.get("line"), "stack": stack}})


def judge(res, healed=HEALED, at=AT, own=None, orig=ORIG):
    return judge_result("e7", res, healed, at, own, [StackFrame.model_validate(f) for f in orig])


def test_a_later_locator_that_finds_nothing_keeps_the_heal():
    assert judge(later(MISSING)) == "ok_later"
    py = 'Timeout 30000ms exceeded.\n=== logs ===\nwaiting for locator("#save") to be visible'
    assert judge(later(py)) == "ok_later"
    in_a_later_test_line = [{"file": "pages/cart_page.py", "line": 3}, {"file": "tests/test_login.py", "line": 5}]
    assert judge(later(MISSING, in_a_later_test_line)) == "ok_later"


@pytest.mark.parametrize("stack", [
    [{"file": "pages/login_page.py", "line": 4}, {"file": "tests/test_login.py", "line": 4}],     # earlier in the method
    [{"file": "pages/cart_page.py", "line": 3}, {"file": "tests/test_login.py", "line": 3}],      # an earlier test line
    [{"file": "pages/cart_page.py", "line": 3}, {"file": "tests/test_login.py", "line": 4}],      # same test line, other file
    [{"file": "tests/conftest.py", "line": 9}],                                                    # a hook: other outermost file
    [],                                                                                            # no place at all
])
def test_a_failure_that_is_not_provably_after_the_healed_line_keeps_nothing(stack):
    assert judge(later(MISSING, stack, "pages/x.py", 1)) == "rerun_failed"


def test_without_a_stack_or_an_original_stack_nothing_is_later():
    body = {"count": 1, "hit": {"ref": "e7"}, "test_outcome": "failed_later", "later": {"error": MISSING, "file": "a", "line": 9}}
    assert judge(ResultRequest.model_validate(body)) == "rerun_failed"
    assert judge(later(MISSING), orig=[]) == "rerun_failed"


@pytest.mark.parametrize("error", [
    "expect(locator).toHaveText(expected) failed\nLocator: locator('#total')\nExpected: \"3\"\nReceived: \"4\"\n"
    "Timeout 5000ms exceeded.\n  - waiting for locator('#total')\n  - locator resolved to <span>4</span>\n"
    "  - unexpected value \"4\"",
    "expect(received).toBe(expected)\nExpected: 3\nReceived: 4",
    "Error: net::ERR_CONNECTION_REFUSED at http://localhost:3000/",
    "locator.click: Error: strict mode violation: locator('#save') resolved to 2 elements",
])
def test_a_later_failure_that_is_not_a_missing_locator_does_not_keep_the_heal(error):
    assert judge(later(error)) == "rerun_failed"


def test_a_later_failure_on_the_healed_locator_or_line_is_the_heal_failing():
    on_healed = 'locator.fill: Timeout 5000ms exceeded.\n  - waiting for get_by_test_id("login-user")'
    assert judge(later(on_healed)) == "rerun_failed"
    same_line = [{"file": "pages/login_page.py", "line": 6}, {"file": "tests/test_login.py", "line": 4}]
    assert judge(later(MISSING, same_line)) == "rerun_failed"
    assert judge(later(MISSING), None, None) == "rerun_failed"     # nothing to compare with: not kept


def test_a_bare_file_name_is_not_the_healed_file():
    stack = [{"file": "login_page.py", "line": 12}, {"file": "tests/test_login.py", "line": 4}]
    assert judge(later(MISSING, stack)) == "rerun_failed"


def test_failed_later_still_needs_the_right_element_and_count():
    body = {"count": 1, "hit": {"ref": "e9"}, "test_outcome": "failed_later", "later": {"error": MISSING}}
    assert judge(ResultRequest.model_validate(body)) == "wrong_element"


def test_a_later_failure_past_the_healed_statements_own_check_keeps_the_heal_whatever_it_is():
    mismatch = "expect(received).toBe(expected)\nExpected: 3\nReceived: 4"
    at, orig = ("tests/cart.spec.ts", 6), [{"file": "tests/cart.spec.ts", "line": 6}]
    def at_line(n, f="tests/cart.spec.ts"):
        return later(mismatch, [{"file": f, "line": n}])
    assert judge(at_line(9), at=at, own=7, orig=orig) == "ok_later"
    assert judge(at_line(7), at=at, own=7, orig=orig) == "rerun_failed"            # its own check
    assert judge(at_line(9, "tests/other.spec.ts"), at=at, own=7, orig=orig) == "rerun_failed"
    assert judge(at_line(9), at=at, own=None, orig=orig) == "rerun_failed"


@pytest.mark.parametrize("source, end_line, want", [
    ("a\nawait page.click(x)\nawait expect(y).toBeVisible()\nz\n", 2, 3),
    ("a\npage.click(x)\n\n# note\nexpect(y).to_be_visible()\n", 2, 5),
    ("a\npage.click(x)\n// note\nawait expect(y).toBeVisible()\n", 2, 4),
    ("a\npage.click(x)\npage.fill(z)\nexpect(y)\n", 2, None),      # another statement first: no check of its own
    ("a\npage.click(x)\n", 2, None),
    # a check split over lines ends on its last line (Playwright reports the failure on the matcher's line)
    ("a\npage.click(x)\nawait expect(\n  page.getByText('Saved (1)'),\n).toBeVisible();\nz\n", 2, 5),
    ("a\npage.click(x)\nexpect(page.locator('#a'))\\\n    .to_have_text(')')\nz\n", 2, 4),
    ("a\npage.click(x)\nawait expect(page.locator('#a'))\n  .toHaveText('x');\nz\n", 2, 4),
    ("a\npage.click(x)\nawait expect(\n  page.getByText('x'),\n", 2, None),            # never closes: unknown
    ("a\npage.click(x)\u2028\nawait expect(y).toBeVisible()\n", 2, 3),               # only \n ends a line
])
def test_own_check_line_is_the_last_line_of_the_first_expect_right_after_the_statement(source, end_line, want):
    assert own_check_line(source, end_line) == want


def test_the_code_frame_a_ts_error_prints_is_not_read_as_the_failing_locator():
    # Playwright quotes the lines around the failure; the healed line above it is quoted too, and must not count as named
    error = ("TimeoutError: locator.click: Timeout 1500ms exceeded.\nCall log:\n"
             "  - waiting for getByRole('button', { name: 'Save changes' })\n\n\n"
             "  11 |   await page.locator('input[data-pref=\"newsletter\"]').check();\n"
             "> 12 |   await page.getByRole('button', { name: 'Save changes' }).click();\n"
             "     |                                                            ^\n"
             "  13 |   await expect(page.getByTestId('summary')).toHaveText('saved');\n"
             "    at /privatetests/drift.spec.ts:12:60")
    healed = "page.locator('input[data-pref=\"newsletter\"]')"
    stack = [{"file": "tests/drift.spec.ts", "line": 12}]
    assert judge(later(error, stack), healed, ("tests/drift.spec.ts", 11), None, [{"file": "tests/drift.spec.ts", "line": 11}]) \
        == "ok_later"
    on_healed = error.replace("waiting for getByRole('button', { name: 'Save changes' })",
                              "waiting for locator('input[data-pref=\"newsletter\"]')")
    assert judge(later(on_healed, stack), healed, ("tests/drift.spec.ts", 11), None,
                 [{"file": "tests/drift.spec.ts", "line": 11}]) == "rerun_failed"


def test_a_code_frame_alone_does_not_make_a_missing_locator():
    frame_only = "Error: boom\n> 12 |   await page.locator('#a').click();\n     |   ^\n  13 |   waiting for x Timeout"
    assert not is_missing_locator(frame_only)


def test_stripping_the_code_frame_is_linear_on_long_blank_lines():
    import time
    t = time.perf_counter()
    is_missing_locator(" " * 50000 + "\n" + " " * 50000 + "x")
    assert time.perf_counter() - t < 0.5
