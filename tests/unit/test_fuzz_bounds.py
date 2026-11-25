"""
===============================================================================
Fuzz: no public text-scanning function may stall or exhaust memory on hostile input
===============================================================================
Every function of the pipeline that scans client text (source, error text, locator expressions, aria snapshots,
inventory fields) is fed hostile 100 KB inputs: unclosed quotes, backslash runs, whitespace runs, nested parentheses
and brackets, lone surrogates, huge counted repetitions. Each call must finish under BOUND_S seconds (asserted inside
the test, from a monotonic clock) and may raise only ValueError (which includes our refusal types) or
rx.CannotEvaluate: never MemoryError, RecursionError, UnicodeError or anything else.

CASES is a plain list of (label, callable(text)) so a new public function is added with one line.
"""
import faulthandler
import time

import pytest

from voyageur import (element_facts, elements, failing, inventory, locator_policy, locator_steps, locators, pick,
                          redact, rx, triage, uniqueness)
from voyageur.schemas import SourceFile

pytestmark = pytest.mark.unit

BOUND_S = 2.0
N = 100_000

# a watchdog so a regression that really hangs fails the run instead of freezing it
faulthandler.dump_traceback_later(900, exit=True)

HOSTILE = {
    "unclosed_locator_quote": ("page.locator('\\'" * (N // 16)),
    "unclosed_double": ('page.get_by_text("\\"' * (N // 20)),
    "regex_noncapture_quotes": "'(?:" * (N // 4),
    "whitespace": " " * N,
    "whitespace_tail": "page.locator(" + " " * N,
    "nested_parens": "page.locator(" + "(" * N,
    "nested_calls": "page" + ".locator(" * (N // 9),
    "nested_filter": "page.locator('a')" + ".filter({ has: page.locator('b')" * (N // 40),
    "backslashes": "\\" * N,
    "backslash_quote": "page.getByText('" + "\\" * N,
    "surrogates": "\ud800" * N,
    "surrogate_name": 'page.getByRole("button", { name: "a\ud800" })',
    "huge_counted": "page.getByText(/" + "a{1,1000}" * (N // 9) + "/)",
    "counted_alt": "page.getByText(/(.{0,900}|b)*X/)",
    "deep_brackets": "[" * N,
    "deep_braces": "{" * N,
    "role_calls": "page.getByRole('button', { name: " * (N // 33),
    "role_tail": "page.getByRole('a'," + " " * N + ")",
    "dots": "." * N,
    "newlines": "\n" * N,
    "stack_frames": "    at /a/b.ts:1:1\n" * (N // 20),
    "identifier_run": "a" * N,
    "dotted_identifiers": "ab." * (N // 3),
    "at_parens": "    at " + "x(" * (N // 2),
    "at_lines_long": ("    at " + "(a" * 300 + "\n") * (N // 600),
    "dashes": "- button " + '"' * N,
}

ARIA_BAD = ['- button "a\ud800"\n', "- " + "button " * 20_000 + "\n", '- button "' + "\\" * N + '\n',
            "\n".join(['- row:'] * 20_000), ("  " * 5000) + '- button "x"\n']


def _txt(s):
    return s[:N]


def _file(s):
    return SourceFile(path="tests/t.ts", content=_txt(s))


def _el(s):
    return {"tag": "button", "role": "button", "name": s[:400], "text": s[:400], "label": s[:400],
            "placeholder": s[:400], "id": s[:400], "testid": s[:400], "class": s[:400], "hidden": False}


def _inv(s, aria=None):
    return inventory.Inventory([_el(s)] * 50, True, aria if aria is not None else s)


def _facts(s):
    return locator_policy.facts_of(_el(s))


CALL = "page.getByRole('button', { name: 'Save' })"

CASES = [
    # locators
    ("locators.broken_selector_from", lambda s: locators.broken_selector_from(_txt(s))),
    ("locators.selectors_from", lambda s: locators.selectors_from(_txt(s))),
    ("locators.pick_broken", lambda s: locators.pick_broken(_txt(s), _txt(s))),
    ("locators.find_call", lambda s: locators.find_call(_txt(s), "#a")),
    ("locators.find_call_sel", lambda s: locators.find_call(_txt(s), _txt(s))),
    ("locators.receiver_of", lambda s: locators.receiver_of(_txt(s))),
    ("locators.strip_strings", lambda s: locators.strip_strings(_txt(s))),
    ("locators.other_spelling", lambda s: locators.other_spelling(_txt(s), "python")),
    ("locators.ts_chain_reason", lambda s: locators.ts_chain_reason(_txt(s))),
    ("locators.ts_chain_reason_nolit", lambda s: locators.ts_chain_reason(_txt(s), False)),
    ("locators.py_chain_reason", lambda s: locators.py_chain_reason(_txt(s))),
    ("locators.py_chain_reason_nolit", lambda s: locators.py_chain_reason(_txt(s), False)),
    ("locators.underscore_attribute", lambda s: locators.underscore_attribute(_txt(s))),
    ("locators.validate_replacement_py", lambda s: locators.validate_replacement(_txt(s), _txt(s), "python")),
    ("locators.validate_replacement_ts", lambda s: locators.validate_replacement(CALL, _txt(s), "typescript")),
    ("locators.validate_replacement_old", lambda s: locators.validate_replacement(_txt(s), CALL, "typescript")),
    ("locators.is_valid_replacement", lambda s: locators.is_valid_replacement(_txt(s), _txt(s), "typescript")),
    # locator_steps
    ("locator_steps.parse_ts_chain", lambda s: locator_steps.parse_ts_chain(_txt(s))),
    ("locator_steps.parse_py_chain", lambda s: locator_steps.parse_py_chain(_txt(s))),
    ("locator_steps.parse_chain", lambda s: locator_steps.parse_chain(_txt(s), "typescript")),
    ("locator_steps.try_parse_ts", lambda s: locator_steps.try_parse(_txt(s), "typescript")),
    ("locator_steps.try_parse_py", lambda s: locator_steps.try_parse(_txt(s), "python")),
    # failing
    ("failing.norm", lambda s: failing.norm(_txt(s))),
    ("failing.balanced_end", lambda s: failing.balanced_end(_txt(s), s.find("(") if "(" in s else 0)),
    ("failing.iter_calls", lambda s: failing.iter_calls(_txt(s))),
    ("failing.printed_of", lambda s: failing.printed_of(_txt(s))),
    ("failing.printed_expr", lambda s: failing.printed_expr(_txt(s))),
    ("failing.strict_count", lambda s: failing.strict_count(_txt(s))),
    ("failing.frame_lines", lambda s: failing.frame_lines(_txt(s), "t.ts")),
    ("failing.frame_lines_any", lambda s: failing.frame_lines(_txt(s))),
    ("failing.locate_failing", lambda s: failing.locate_failing(_txt(s), _txt(s), "t.ts")),
    ("failing.locate_failing_err", lambda s: failing.locate_failing(_txt(s), CALL * 10, "t.ts")),
    ("failing.locate_failing_src", lambda s: failing.locate_failing("waiting for getByRole('button')", _txt(s))),
    ("failing.split_failures", lambda s: failing.split_failures(_txt(s))),
    ("failing.is_assertion_mismatch", lambda s: failing.is_assertion_mismatch(_txt(s))),
    ("failing.locate_all", lambda s: failing.locate_all(_txt(s), _txt(s), "t.ts")),
    ("failing.failing_set", lambda s: failing.failing_set(_txt(s), "t.ts")),
    ("failing.has_locator_signal", lambda s: failing.has_locator_signal(_txt(s))),
    ("failing.failure_identity", lambda s: failing.failure_identity(_txt(s), "t.ts")),
    ("failing.excerpt", lambda s: failing.excerpt(_txt(s))),
    # uniqueness
    ("uniqueness.normalize_space", lambda s: uniqueness.normalize_space(_txt(s))),
    ("uniqueness.broad_reason", lambda s: uniqueness.broad_reason(_txt(s))),
    ("uniqueness.aria_count_expr", lambda s: uniqueness.aria_count(_txt(s), '- button "x"\n')),
    ("uniqueness.aria_count_aria", lambda s: uniqueness.aria_count("page.getByRole('button')", _txt(s))),
    ("uniqueness.aria_count_name", lambda s: uniqueness.aria_count(
        "page.getByRole('button', { name: /" + s[:3000] + "/ })", '- button "' + s[:3000] + '"\n' * 50)),
    ("uniqueness.aria_count_surrogate", lambda s: uniqueness.aria_count(
        "page.getByRole('button', { name: /x/ })", s if "\ud800" in s else '- button "a\ud800"\n')),
    ("uniqueness.ts_expr_is_safe", lambda s: uniqueness.ts_expr_is_safe(_txt(s))),
    ("uniqueness.py_expr_is_safe", lambda s: uniqueness.py_expr_is_safe(_txt(s))),
    ("uniqueness.missing", lambda s: uniqueness.Uniqueness().missing(_txt(s), _inv(s))),
    ("uniqueness.ambiguity", lambda s: uniqueness.Uniqueness().ambiguity(_txt(s), "typescript", "/", _txt(s), _inv(s))),
    ("uniqueness.role_hint", lambda s: uniqueness.Uniqueness().missing(
        "page.getByRole('button', { name: 'z' })", inventory.Inventory([_el("q")], True, _txt(s)))),
    # inventory
    ("inventory.count", lambda s: _inv("x").count(_txt(s))),
    ("inventory.count_aria", lambda s: _inv(s, _txt(s)).count("page.getByRole('button', { name: 'a' })")),
    ("inventory.count_text", lambda s: _inv(s, _txt(s)).count("page.getByText(/a.*b/)")),
    ("inventory.count_fields", lambda s: _inv(s).count("page.getByText('" + s[:300] + "')")),
    ("inventory.count_regex_fields", lambda s: _inv(s).count("page.getByText(/(.{0,900}|b)*X/)")),
    ("inventory.count_re_compile", lambda s: _inv(s).count("page.get_by_text(re.compile('(a+)+$'))")),
    ("inventory.matched", lambda s: _inv("x").matched(_txt(s))),
    ("inventory.matched_fields", lambda s: _inv(s).matched("page.getByLabel('" + s[:300] + "').first")),
    ("inventory.init", lambda s: inventory.Inventory([_el(s)] * 500, True, _txt(s))),
    ("inventory._unescape", lambda s: inventory._unescape(_txt(s))),
    ("inventory._lit_and_opts", lambda s: inventory._lit_and_opts(_txt(s))),
    # locator_policy
    ("locator_policy.is_generated_id", lambda s: locator_policy.is_generated_id(_txt(s))),
    ("locator_policy.parse_order", lambda s: locator_policy.parse_order(_txt(s))),
    ("locator_policy.candidates", lambda s: locator_policy.candidates(_facts(s), "typescript")),
    ("locator_policy.candidates_py", lambda s: locator_policy.candidates(_facts(s), "python")),
    ("locator_policy.kind_of", lambda s: locator_policy.kind_of(_txt(s))),
    ("locator_policy.page_language", lambda s: locator_policy.page_language({"html_lang": _txt(s)})),
    ("locator_policy.facts_of", lambda s: locator_policy.facts_of(_el(s))),
    ("locator_policy.stabilize", lambda s: locator_policy.stabilize(_txt(s), _inv(s), "typescript", "page")),
    ("locator_policy.same_element", lambda s: locator_policy.same_element(_inv(s), _txt(s), _el(s))),
    ("locator_policy.locale_branch", lambda s: locator_policy.locale_branch(_txt(s), _txt(s), "fr", "python")),
    ("locator_policy.locale_branch_ts", lambda s: locator_policy.locale_branch(_txt(s), _txt(s), "fr", "typescript")),
    ("locator_policy.copy_based", lambda s: locator_policy.copy_based(_txt(s))),
    ("locator_policy.write_facts", lambda s: locator_policy.write_facts(_el(s), "data-testid")),
    ("locator_policy.write", lambda s: locator_policy.write(_el(s), _inv(s), "typescript", "page", "role",
                                                            broken_call=_txt(s))),
    # redact
    ("redact.redact_urls", lambda s: redact.redact_urls(_txt(s))),
    ("redact.scrub_secrets", lambda s: redact.scrub_secrets(_txt(s), "secretvalue")),
    ("redact.redact_typed_values", lambda s: redact.redact_typed_values(_txt(s))),
    ("redact.redact_typed_values_textbox", lambda s: redact.redact_typed_values('- textbox "' + _txt(s))),
    # pick
    ("pick.strip_invisible", lambda s: pick.strip_invisible(_txt(s))),
    ("pick.clean", lambda s: pick.clean(_txt(s))),
    ("pick.candidate_line", lambda s: pick.candidate_line(_el(s))),
    ("pick.hint_of", lambda s: pick.hint_of(_txt(s))),
    ("pick.prefilter", lambda s: pick.prefilter([_el(s)] * 500, "click", pick.hint_of(_txt(s)), 40)),
    ("pick.redact_input_literals", lambda s: pick.redact_input_literals(_txt(s))),
    ("pick.context_lines", lambda s: pick.context_lines(_txt(s), 1)),
    ("pick.build_prompt", lambda s: pick.build_prompt(pick.context_lines(_txt(s), 1), s[:64], [s[:300]] * 40)),
    ("pick.parse_pick", lambda s: pick.parse_pick(_txt(s), {"a"})),
    # triage
    ("triage.triage_error", lambda s: triage.triage(_txt(s), [_file(CALL)], [("t.ts", 1)], "typescript")),
    ("triage.triage_source", lambda s: triage.triage("waiting for getByRole('button', { name: 'Save' })",
                                                     [_file(s)], [("t.ts", 1)], "typescript")),
    ("triage.triage_both", lambda s: triage.triage(_txt(s), [_file(s)], [("t.ts", 1)], "typescript")),
    # elements
    ("elements.norm_text", lambda s: elements.norm_text(_txt(s))),
    ("elements.same_inventory_element", lambda s: elements.same_inventory_element(_el(s), _el(s))),
    ("elements.identity_match", lambda s: elements.identity_match(_el(s), _el(s), [_el(s)] * 500)),
    ("elements.could_be_baseline_control", lambda s: elements.could_be_baseline_control(_el(s), _el(s))),
    ("elements.describe_control", lambda s: elements.describe_control(_el(s))),
    ("elements.proof_of", lambda s: elements.proof_of(_el(s), _el(s), [_el(s)] * 200, None)),
    ("elements.compatible", lambda s: elements.compatible(_txt(s), _el(s))),
    ("elements.stable_keys", lambda s: elements.stable_keys(_el(s), _el(s))),
    # element_facts
    ("element_facts.element_kind", lambda s: element_facts.element_kind(_el(s))),
    ("element_facts.copy_of", lambda s: element_facts.copy_of(_el(s))),
    ("element_facts.subject_tag", lambda s: element_facts.subject_tag(_txt(s))),
    # rx
    ("rx.search", lambda s: rx.search(s[:200], False, _txt(s))),
    ("rx.search_counted", lambda s: rx.search("(.{0,900}|b)*X", False, _txt(s))),
    ("rx.search_surrogate", lambda s: rx.search("x", False, s)),
]

_ALLOWED = (ValueError, rx.CannotEvaluate)


@pytest.mark.parametrize("hostile", sorted(HOSTILE))
@pytest.mark.parametrize("label,call", CASES, ids=[c[0] for c in CASES])
def test_hostile_input_is_bounded(label, call, hostile):
    text = HOSTILE[hostile]
    started = time.monotonic()
    try:
        call(text)
    except _ALLOWED as exc:
        assert not isinstance(exc, UnicodeError), f"{label} leaked {type(exc).__name__}"
    elapsed = time.monotonic() - started
    assert elapsed < BOUND_S, f"{label} took {elapsed:.2f}s on {hostile}"


@pytest.mark.parametrize("aria", range(len(ARIA_BAD)))
def test_hostile_aria_snapshots_are_bounded(aria):
    snap = ARIA_BAD[aria]
    started = time.monotonic()
    for expr in ("page.getByRole('button')", "page.getByRole('button', { name: /x/ })",
                 "page.getByRole('row', { name: 'a' })"):
        uniqueness.aria_count(expr, snap)
    inventory.Inventory([_el("a")], True, snap).count("page.getByText('a')")
    redact.redact_typed_values(snap)
    assert time.monotonic() - started < BOUND_S


def test_lone_surrogate_in_a_snapshot_with_a_regex_name_is_not_an_error():
    # repro: RE2 raised UnicodeEncodeError inside search
    got = uniqueness.aria_count("page.getByRole('button', { name: /x/ })", '- button "a\ud800"\n')
    assert got in (None, 0)
    with pytest.raises(rx.CannotEvaluate):
        rx.search("x", False, "a\ud800")


def test_counted_repetition_with_a_big_bound_is_unknown_and_fast():
    started = time.monotonic()
    with pytest.raises(rx.CannotEvaluate):
        rx.search("(.{0,900}|b)*X", False, "b" * 2_000_000)
    assert time.monotonic() - started < BOUND_S
    assert uniqueness.aria_count("page.getByRole('button', { name: /(.{0,900}|b)*X/ })",
                                 '- button "' + "b" * 400 + '"\n' * 3000) is None


def test_total_regex_work_per_count_call_is_capped():
    inv = inventory.Inventory([{"tag": "button", "text": "b" * 400, "name": "b" * 400}] * 500, True)
    started = time.monotonic()
    assert inv.count("page.getByText(/(b|bb)*X/)") in (None, 0)
    assert time.monotonic() - started < BOUND_S
    snap = "".join('- button "' + "b" * 400 + '"\n' for _ in range(5000))
    started = time.monotonic()
    uniqueness.aria_count("page.getByRole('button', { name: /(b|bb)*X/ })", snap)
    assert time.monotonic() - started < BOUND_S


def test_role_call_expression_is_linear():
    for n in (8_000, 32_000, 100_000):
        expr = "page.getByRole('button'," + " " * n + ")"
        started = time.monotonic()
        uniqueness.aria_count(expr, '- button "x"\n')
        inventory.Inventory([_el("a")], True, '- button "x"\n').count(expr)
        assert time.monotonic() - started < 0.5, n


def test_over_long_expression_is_unknown_not_truncated():
    big = "page.getByRole('button', { name: '" + "a" * 5000 + "' })"
    assert uniqueness.aria_count(big, '- button "' + "a" * 5000 + '"\n') is None
    assert inventory.Inventory([_el("a")], True).count(big) is None


def test_unclosed_locator_quotes_scan_is_linear():
    times = []
    for n in (6_000, 25_000, 100_000):
        src = "page.locator('\\'" * (n // 15)
        started = time.monotonic()
        failing.iter_calls(src)
        times.append(time.monotonic() - started)
    assert max(times) < BOUND_S
    assert times[-1] < 0.5 + 8 * max(times[0], 0.01) * 4        # roughly linear in size, never quadratic
