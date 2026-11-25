"""
===============================================================================
Not-found detection stays linear
===============================================================================
An assertion mismatch is told apart from a missing element by a "waiting for ... visible" line. That check used a
regex that backtracked once per repeat of "waiting for", so a hostile 64 KB error took seconds.
"""
import time

from voyageur.failing import _not_found, is_assertion_mismatch


def test_a_hostile_error_built_from_waiting_for_repeats_is_checked_fast():
    hostile = ("waiting for " * 700)[:8000] * 8
    t = time.perf_counter()
    assert _not_found(hostile) is False
    assert time.perf_counter() - t < 0.2


def test_not_found_keeps_its_meaning():
    assert _not_found('waiting for locator("#a") to be visible')
    assert _not_found("Error: element(s) not found")
    assert not _not_found('waiting for locator("#a")')


def test_a_resolved_locator_with_a_value_mismatch_is_still_an_assertion_mismatch():
    block = 'locator resolved to <span>3</span>\nExpected string: "4"\nReceived string: "3"'
    assert is_assertion_mismatch(block)
    assert not is_assertion_mismatch(block + '\nwaiting for locator("#n") to be visible')
