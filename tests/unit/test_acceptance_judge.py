"""
===============================================================================
Acceptance judge tests (how the bench stand-in client judges the re-run after a heal)
===============================================================================
The bench reports the re-run as a real client does: passed, failed, or failed_later (stopped past the patched line) with the
later failure. The service judges failed_later (gates.judge_result, tested in test_gates.py); these tests pin the bench's side
and run the service's judge over the bench's own example suite, so both halves agree on the rule.

Author: PMAC
===============================================================================
"""
import importlib.util
from pathlib import Path

import pytest

from voyageur.gates import judge_result, own_check_line
from voyageur.schemas import ResultRequest, StackFrame

pytestmark = pytest.mark.unit

_SPEC = importlib.util.spec_from_file_location("run_acceptance", Path(__file__).parents[2] / "testbed" / "run_acceptance.py")
acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acceptance)

SOURCE = '''def test_it(page):
    # first
    page.locator("#a").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.a")
    # second
    page.locator("#b").click()

    expect(page.get_by_test_id("summary")).to_contain_text("hit.b")
    page.locator("#c").click()
    page.locator("#d").click()
    expect(page.get_by_test_id("summary")).to_contain_text("hit.d")
'''
ASSERT = "AssertionError: Locator expected to contain text 'hit.b'"
TIMEOUT = ('playwright._impl._errors.TimeoutError: Locator.click: Timeout 1500ms exceeded.\nCall log:\n'
           '  - waiting for locator("#d")')


def failed(line, error):
    return {"result": "failed", "line": line, "error": error, "frames": [("tests/test_it.py", line)]}


def judged(line, error, patched_line):
    body = {"count": 1, "hit": {"ref": "e1"}, "test_outcome": "failed_later",
            "later": {"error": error, "file": "tests/test_it.py", "line": line,
                      "stack": [{"file": "tests/test_it.py", "line": line}]}}
    healed = 'page.get_by_test_id("new")'
    return judge_result("e1", ResultRequest.model_validate(body), healed, ("tests/test_it.py", patched_line),
                        own_check_line(SOURCE, patched_line), [StackFrame(file="tests/test_it.py", line=patched_line)])


def test_the_bench_reports_passed_failed_or_failed_later():
    assert acceptance.rerun_outcome({"result": "passed"}, 3) == "passed"
    assert acceptance.rerun_outcome(failed(8, ASSERT), 3) == "failed_later"
    assert acceptance.rerun_outcome(failed(3, TIMEOUT), 3) == "failed"
    assert acceptance.rerun_outcome({"result": "failed", "line": None, "error": "x", "frames": []}, 3) == "failed"


def test_an_assertion_past_the_statements_own_check_is_a_later_statements_drift():
    assert judged(8, ASSERT, 3) == "ok_later"


def test_an_assertion_on_the_statements_own_check_rejects_the_heal():
    assert judged(4, ASSERT, 3) == "rerun_failed"
    assert judged(8, ASSERT, 6) == "rerun_failed"


def test_a_later_locator_that_found_nothing_keeps_the_heal():
    assert judged(10, TIMEOUT, 3) == "ok_later"


def test_a_statement_without_a_check_of_its_own_keeps_only_a_missing_locator():
    assert judged(11, ASSERT, 9) == "rerun_failed"
    assert judged(10, TIMEOUT, 9) == "ok_later"
