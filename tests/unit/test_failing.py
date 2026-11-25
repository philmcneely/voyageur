"""Unit: identify the locator the failure is about (printed expr, frames, strict mode), by exact span."""
import pytest

from voyageur.failing import (excerpt, failure_identity, frame_lines, iter_calls, locate_failing,
                               printed_expr, strict_count)

pytestmark = pytest.mark.unit

SRC = """class LoginPage {
  async login(user, pw) {
    await this.page.getByRole('button', { name: /log(in)/i }).click();
    await this.page.getByLabel(/password/i).fill(pw);
    await this.page.locator('#remember').check();
  }
}"""

STRICT_ERR = """  1) spec.ts:5:5 › login ──────
    Error: locator.fill: Error: strict mode violation: getByLabel(/password/i) resolved to 2 elements:
        1) <input id="pw"/> aka getByLabel('Password')
        2) <button>Show password</button> aka getByRole('button', { name: 'Show password' })

    Call log:
      - waiting for getByLabel(/password/i)

       3 |     await this.page.getByRole('button', { name: /log(in)/i }).click();
    >  4 |     await this.page.getByLabel(/password/i).fill(pw);
         |                                             ^
        at /tmp/x/spec.ts:4:46
"""

TIMEOUT_ERR = """    Error: locator.click: Test timeout of 30000ms exceeded.
    Call log:
      - waiting for getByRole('button', { name: /log(in)/i })

    >  3 |     await this.page.getByRole('button', { name: /log(in)/i }).click();
        at LoginPage.login (/tmp/x/spec.ts:3:60)
"""


def test_scanner_finds_regex_nested_and_chained_calls():
    texts = [c.text for c in iter_calls(SRC)]
    assert texts == ["this.page.getByRole('button', { name: /log(in)/i })", "this.page.getByLabel(/password/i)",
                     "this.page.locator('#remember')"]
    assert [c.line for c in iter_calls(SRC)] == [3, 4, 5]
    chained = iter_calls("await page.locator('form').getByRole('button').filter({ hasText: 'Go' }).first().click();")
    assert chained[0].text == "page.locator('form').getByRole('button').filter({ hasText: 'Go' }).first()"


def test_scanner_ignores_unbalanced_and_strings_with_parens():
    assert iter_calls("page.locator('a)b')")[0].text == "page.locator('a)b')"
    assert iter_calls("page.locator(") == []


def test_printed_expr_from_waiting_for_and_strict():
    assert printed_expr(STRICT_ERR) == "getByLabel(/password/i)"
    assert printed_expr(TIMEOUT_ERR) == "getByRole('button', { name: /log(in)/i })"
    assert printed_expr("assert 1 == 2") is None


def test_strict_count():
    assert strict_count(STRICT_ERR) == 2
    assert strict_count(TIMEOUT_ERR) is None


def test_frame_lines_prefer_test_file_and_skip_heading():
    assert frame_lines(STRICT_ERR, "spec.ts") == [4, 4]          # `at` frame + code frame; heading 5:5 ignored
    assert frame_lines(STRICT_ERR, "other.ts") == [4]            # code frame only; `at` is another file


def test_failure_on_second_locator_targets_the_second_not_the_first():
    t = locate_failing(STRICT_ERR, SRC, "spec.ts")
    assert t.call == "this.page.getByLabel(/password/i)" and t.line == 4 and t.how == "printed"
    assert t.strict_n == 2 and SRC[t.start:t.end] == t.call
    t1 = locate_failing(TIMEOUT_ERR, SRC, "spec.ts")
    assert t1.line == 3 and "getByRole" in t1.call


def test_same_text_twice_is_disambiguated_by_the_frame_line():
    src = "await page.locator('#a').click();\nawait page.locator('#a').fill('x');\n"
    err = "waiting for locator('#a')\n    >  2 | await page.locator('#a').fill('x');\n        at /t/s.ts:2:25\n"
    t = locate_failing(err, src, "s.ts")
    assert t.line == 2 and src[t.start:t.end] == "page.locator('#a')" and t.start > src.index("\n")


def test_frame_fallback_when_nothing_printed():
    err = "Error: locator.click: Test timeout of 30000ms exceeded.\n  at /t/s.ts:2:30\n"
    src = "await page.locator('#a').click();\nawait page.getByText('Go').click();\n"
    t = locate_failing(err, src, "s.ts")
    assert t.how == "frame" and t.line == 2 and "getByText" in t.call


def test_column_picks_the_call_before_it_on_a_crowded_line():
    src = "await page.locator('#a').click(); await page.locator('#b').click();\n"
    err = "Error: Test timeout of 30000ms exceeded.\n  at /t/s.ts:1:62\n"
    assert locate_failing(err, src, "s.ts").call == "page.locator('#b')"


def test_assertion_failure_is_not_treated_as_locator_drift():
    err = "Error: expect(received).toBe(expected)\n    >  4 | expect(1).toBe(2);\n  at /t/s.ts:4:5\n"
    assert locate_failing(err, "await page.locator('#a').click();\n\n\nexpect(1).toBe(2);", "s.ts") is None


def test_unknown_printed_locator_in_other_file_is_not_found():
    err = "waiting for getByLabel('Zip')\n  at /t/pages/Other.ts:9:3\n"
    assert locate_failing(err, SRC, "spec.ts") is None


def test_python_traceback_line_and_legacy_quoted_selector():
    src = "def test_x(page):\n    page.locator('#ok').click()\n    page.locator('#gone').click()\n"
    err = "spec.py:3: in test_x\n    page.locator('#gone').click()\nE   Error: Timeout 30000ms exceeded. waiting for locator '#gone'\n"
    t = locate_failing(err, src, "spec.py")
    assert t.call == "page.locator('#gone')" and t.line == 3


def test_identity_changes_when_the_failure_moves():
    other = STRICT_ERR.replace("getByLabel(/password/i)", "getByLabel(/user/i)")
    assert failure_identity(STRICT_ERR, "spec.ts") != failure_identity(other, "spec.ts")
    assert failure_identity(STRICT_ERR, "spec.ts") == failure_identity(STRICT_ERR, "spec.ts")


def test_excerpt_starts_at_the_failure_not_the_tail():
    err = "noise\n" * 5 + STRICT_ERR + "tail line\n" * 400
    out = excerpt(err, 1800)
    assert "strict mode violation" in out and "resolved to 2 elements" in out and len(out) <= 1800
