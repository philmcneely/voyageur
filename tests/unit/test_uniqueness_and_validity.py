"""Unit: the uniqueness checks (static / aria snapshot) and what a replacement may and may not be."""
import pytest

from voyageur.locators import validate_replacement
from voyageur.uniqueness import (Uniqueness, aria_count, broad_reason, normalize_space, py_expr_is_safe,
                                     ts_expr_is_safe)

pytestmark = pytest.mark.unit

CLOSE5 = "".join(f'- button "Close" [ref=e{i}]\n' for i in range(4)) + '- button "Close and save" [ref=e9]\n- button "Open"\n'

# --- what a replacement may be --------------------------------------------------------------
TS = "this.page.getByRole('button', { name: 'Close', exact: true })"


@pytest.mark.parametrize("good", [
    "this.page.locator('#close')",                                       # role -> id (cross-strategy, same element)
    "this.page.getByTestId('close-btn')",                                # role -> test id
    "this.page.locator('#exampleModal').getByRole('button', { name: 'Close' })",   # scoped to the dialog
    "this.page.locator('.modal-footer button.btn-secondary')",
    "this.page.getByRole('button', { name: 'Close' }).first()",
    "this.page.getByRole('dialog').getByRole('button', { name: 'Close' }).last()",
    "this.page.frameLocator('#f').getByRole('button', { name: 'Close' })",
])
def test_ts_cross_strategy_and_scoped_swaps_are_valid(good):
    assert validate_replacement(TS, good, "typescript") is None


@pytest.mark.parametrize("bad,why", [
    (TS, "identical"),
    ("", "empty"),
    ("this.page.locator('#close').click()", "action"),
    ("this.page.locator('#close').fill('x')", "action"),
    ("this.page.locator('#close'); foo()", "statement"),
    ("this.page.title()", "not a locator"),
    ("this.page.locator('#a')\nthis.page.locator('#b')", "more than one line"),
    ("this.page.locator('#a').evaluate(x => 1)", "action"),
    ("other.locator('#a')", "different receiver"),
])
def test_ts_invalid_rewrites_carry_the_specific_reason(bad, why):
    r = validate_replacement(TS, bad, "typescript")
    assert r is not None and why in r


def test_receiver_may_change_to_a_handle_the_test_already_uses():
    call = "this.modal.getByRole('button', { name: 'Close' })"
    swap = "this.page.locator('#m').getByRole('button', { name: 'Close' })"
    assert "different receiver" in validate_replacement(call, swap, "typescript")
    assert validate_replacement(call, swap, "typescript", {"this.modal", "this.page"}) is None


def test_python_swaps_and_actions():
    call = "page.get_by_role('button', name='Close', exact=True)"
    assert validate_replacement(call, "page.locator('#close')", "python") is None
    assert validate_replacement(call, "page.get_by_test_id('close')", "python") is None
    assert validate_replacement(call, "page.locator('#m').get_by_role('button', name='Close').first", "python") is None
    assert "action" in validate_replacement(call, "page.locator('#close').click()", "python")
    assert "not a valid" in validate_replacement(call, "page.locator(", "python")


# --- static ----------------------------------------------------------------------------------
@pytest.mark.parametrize("css", ['button:has-text("Close")', "button", 'a:text-is("x")', "input", "div:visible"])
def test_bare_tag_selectors_are_broad(css):
    assert "bare" in broad_reason(f"this.page.locator('{css}')")


@pytest.mark.parametrize("css", ["#close", ".modal-footer button", "button#close", "[data-testid=close]", "a.nav-link"])
def test_anchored_selectors_are_not_broad(css):
    assert broad_reason(f"this.page.locator('{css}')") is None


# --- failure-snapshot counting ---------------------------------------------------------------
def test_aria_count_substring_exact_regex_and_narrowing():
    assert aria_count("this.page.getByRole('button', { name: 'Close' })", CLOSE5) == 5          # substring: 4 + 'Close and save'
    assert aria_count("this.page.getByRole('button', { name: 'Close', exact: true })", CLOSE5) == 4
    assert aria_count("this.page.getByRole('button', { name: /^Close$/ })", CLOSE5) == 4
    assert aria_count("this.page.getByRole('button', { name: 'close' })", CLOSE5) == 5          # case-insensitive substring
    assert aria_count("this.page.getByRole('button', { name: 'Close', exact: true }).first()", CLOSE5) == 1
    assert aria_count("this.page.getByRole('button', { name: 'Close', exact: true }).nth(9)", CLOSE5) == 0
    assert aria_count("this.page.getByRole('button', { name: 'Open' })", CLOSE5) == 1
    assert aria_count("page.get_by_role('button', name='Close', exact=True)", CLOSE5) == 4


def test_aria_count_unknown_is_none_not_ok():
    assert aria_count("this.page.locator('#close')", CLOSE5) is None
    assert aria_count("this.page.getByRole('button', { name: 'Close' }).filter({ hasText: 'x' })", CLOSE5) is None
    assert aria_count("this.page.getByRole('button', { name: 'Close' })", "") is None


def test_aria_count_never_runs_a_catastrophic_or_malformed_name_regex():
    import time
    yaml = '- button "' + "a" * 28 + '!"\n'
    t0 = time.monotonic()
    # changed with the move to RE2: the old test expected the heuristic's "no claim" for the shapes that backtrack, but
    # RE2 runs them in linear time and returns the true count; only malformed / backreference patterns are unknown
    for rx, want in (("(a+)+$", 0), ("(a|aa)*$", 1)):
        assert aria_count("page.getByRole('button', { name: /" + rx + "/ })", yaml) == want
    for rx in ("[", "(", "(a)\\1"):             # malformed / a real backreference: RE2 refuses it, so no claim
        assert aria_count("page.getByRole('button', { name: /" + rx + "/ })", yaml) is None
    assert time.monotonic() - t0 < 1
    assert aria_count("page.getByRole('button', { name: /^a+!$/ })", yaml) == 1


# --- Uniqueness ordering ----------------------------------------------------------------------
class FakeLive:
    def __init__(self, counts):
        self.counts, self.asked = counts, []

    def count(self, expr, language, route):
        self.asked.append((expr, language, route))
        return self.counts.get(expr)


def test_uniqueness_static_then_snapshot_then_live():
    live = FakeLive({"this.page.locator('#dup')": 3, "this.page.locator('#one')": 1})
    u = Uniqueness(live=live)
    assert "bare" in u.ambiguity("this.page.locator('button:has-text(\"Close\")')", "typescript", "/", "")
    assert "5 elements in the page at the failure" in u.ambiguity("this.page.getByRole('button', { name: 'Close' })", "typescript", "/", CLOSE5)
    assert "3 elements on the live page" in u.ambiguity("this.page.locator('#dup')", "typescript", "/x", "")
    assert u.ambiguity("this.page.locator('#one')", "typescript", "/", "") is None            # unique
    assert u.ambiguity("this.page.locator('#unknown')", "typescript", "/", "") is None         # unknown is not ambiguous
    assert live.asked[0][2] == "/x"
    assert u.ambiguity("this.page.getByRole('button', { name: 'Close' }).first()", "typescript", "/", CLOSE5) is None


def test_static_can_be_turned_off():
    assert Uniqueness(static=False).ambiguity("this.page.locator('button')", "typescript") is None


# --- whitelisted evaluation --------------------------------------------------------------------
@pytest.mark.parametrize("expr", [
    "page.getByRole('button', { name: 'Close', exact: true })",
    "page.locator('#m').getByRole('button', { name: /close/i }).first()",
    "page.locator('div', { has: page.locator('#a') }).nth(1)",
])
def test_ts_safe_expressions(expr):
    assert ts_expr_is_safe(expr)


@pytest.mark.parametrize("expr", [
    "page.locator(process.exit(1))", "page.locator('#a'); process.exit()", "page.locator(require('fs'))",
    "page.locator('#a').evaluate(x => 1)", "page.locator(`a${b}`)", "page.getByRole('b', { name: foo })",
    "process.exit()", "page.locator('#a') + 1", "page.locator(eval('1'))",
])
def test_ts_unsafe_expressions_are_never_evaluated(expr):
    assert not ts_expr_is_safe(expr)


def test_python_safe_and_unsafe():
    assert py_expr_is_safe("page.locator('#a').nth(1)")
    assert py_expr_is_safe("page.get_by_role('button', name='Close', exact=True)")
    assert not py_expr_is_safe("page.locator(__import__('os').system('x'))")
    assert not py_expr_is_safe("page.locator('#a').click()")
    assert not py_expr_is_safe("open('f')")
    assert not py_expr_is_safe("page.locator(lambda: 1)")



# --- a locator is read as data and never evaluated ----------------------------------------------
BYPASSES = [
    "page.locator('a')['constructor']['constructor']('return process.env')()",       # review item 6
    "page.locator('a')[`constructor`][`constructor`](`return process.env`)()",
    "page.locator('a')['con' + 'structor']",
    "page.locator('a')[(() => 'constructor')()]",
    "page.locator(`a${process.env.GH_TOKEN}`)",
    "page.locator(`a${'x'}`)",
    "page.getByRole('button', { ['name']: 'x' })",
    "page.getByRole('button', { [process.env.GH_TOKEN]: 'x' })",
    "page.getByRole('button', { ...process.env })",
    "page.getByRole('button', { name })",
    "page.getByRole('button', { name: process.env.GH_TOKEN })",
    "page.getByRole('button', { __proto__: { polluted: true } })",
    "page.locator('a').constructor.constructor('return 1')()",
    "page.locator('a')?.first()",
    "page.locator('a').first().click()",
    "page.locator('a'), process.exit(1)",
    "page.locator('a') /* x */",
    "page.locator('a') // x",
    "(page).locator('a')",
    "that.page.locator('a')",
    "await page.locator('a')",
    "page.locator('a');",
    "page.locator('a')\nprocess.exit(1)",
    "page['locator']('a')",
    "page.locator('a').filter({ has: process })",
    "page.locator('a').and(globalThis.x)",
]


@pytest.mark.parametrize("expr", BYPASSES)
def test_ts_gate_rejects_every_bypass_on_its_own(expr):
    # the gate must hold WITHOUT locators.validate_replacement in front of it
    assert not ts_expr_is_safe(expr)



@pytest.mark.parametrize("expr", [
    "page.locator('a').__class__.__base__",
    "page.locator('a')[0]",
    "page.locator(f'{x}')",
    "page.locator('a' + 'b')",
    "page.locator(*args)",
    "page.locator(**kw)",
    "page.locator('a').filter(has_text=open('f'))",
    "page.locator('a').nth(1).click()",
    "page.locator(b'a')",
    "page.locator('a') if x else y",
    "[page.locator('a')]",
    "(lambda: page.locator('a'))()",
    "page.locator('a').first()",
    "page.locator('a').first.click()",
])
def test_python_gate_rejects_bypasses(expr):
    assert not py_expr_is_safe(expr)


def test_normalize_space_collapses_whitespace_and_keeps_case():
    assert normalize_space("  Close \n\t Now ") == "Close Now"

