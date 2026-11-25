"""
===============================================================================
Contract Replay Test (every fixture, both languages, exactly)
===============================================================================
Replays each case of fixtures/contract through the API with its scripted model and asserts the answers equal expected.json
exactly, for the Python and the TypeScript request of the case. A second check holds the two languages to the same decisions
(status, reason, steps, tier movement, expectation), so only the rendered code and chain spelling may differ. Cases that name
prompt checks also assert what the model was and was not sent. Any change to the service that moves a recorded answer fails here,
and the fixture diff is then reviewed on purpose, because clients in other languages replay the same files.

Features:
    ✓ One parametrised test per case and language, one agreement test per case, one prompt-check test per case
    ✓ The required case list is pinned, so a case cannot be dropped silently
    ✓ No fixture may carry a typed value, a cookie or a host

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import json
import re

import pytest

from tests.contract.replay import LANGUAGES, case_dirs, run_case

pytestmark = pytest.mark.unit

CASES = case_dirs()
REQUIRED = {"renamed_id_to_testid", "fingerprint_match_zero_tokens", "text_pick_copy_changed", "not_drift",
            "ambiguous_source_needs_human", "target_removed_needs_human", "french_page_language_branch",
            "tier_floor_kept_id_stays_id", "tier_floor_down_flagged", "count_mismatch_next_candidate",
            "wrong_element_repick", "budget_exhausted", "prompt_injection_in_page_text"}
DECISION = ("status", "round", "reason", "settled_at", "ended_at", "cache_hit", "usage")
CANDIDATE_DECISION = ("target_ref", "strategy", "tier", "tier_movement", "below_floor", "expect")


def test_every_required_case_is_present_and_complete():
    assert REQUIRED <= {c.name for c in CASES}
    for c in CASES:
        for name in ("request.json", "request.typescript.json", "script.json", "expected.json"):
            assert (c / name).exists(), f"{c.name} lacks {name}"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
@pytest.mark.parametrize("language", LANGUAGES)
def test_replay_matches_expected_exactly(case, language):
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))[language]
    assert run_case(case, language) == expected


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_both_languages_reach_the_same_decisions(case):
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    py, ts = expected["python"], expected["typescript"]
    assert len(py) == len(ts)
    for a, b in zip(py, ts):
        assert {k: a[k] for k in DECISION} == {k: b[k] for k in DECISION}
        assert ("candidate" in a) == ("candidate" in b)
        if "candidate" in a:
            assert {k: a["candidate"][k] for k in CANDIDATE_DECISION} == {k: b["candidate"][k] for k in CANDIDATE_DECISION}
            assert (a["candidate"]["branches"] is None) == (b["candidate"]["branches"] is None)
            assert a["candidate"]["locator"].get("kind") == b["candidate"]["locator"].get("kind")


# only the cases that name prompt checks; a case with none has nothing to check here
PROMPT_CASES = [c for c in CASES if json.loads((c / "script.json").read_text(encoding="utf-8")).get("checks")]


@pytest.mark.parametrize("case", PROMPT_CASES, ids=lambda c: c.name)
def test_prompt_checks_named_by_the_case(case):
    checks = json.loads((case / "script.json").read_text(encoding="utf-8"))["checks"]
    prompts = []
    run_case(case, "python", prompts)
    assert prompts, "the case names prompt checks but the model was never called"
    for text in checks.get("prompt_contains", []):
        assert any(text.replace("\n", " ") in p or text in p for p in prompts)
    for pattern in checks.get("prompt_absent_regex", []):
        assert not any(re.search(pattern, p) for p in prompts)


def test_no_fixture_asks_to_send_a_typed_value_or_a_host():
    for case in CASES:
        for f in case.iterdir():
            text = f.read_text(encoding="utf-8")
            assert '"value"' not in text and "cookie" not in text.lower(), f"{case.name}/{f.name}"
