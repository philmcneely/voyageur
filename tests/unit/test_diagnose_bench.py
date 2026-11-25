"""
===============================================================================
Diagnose bench tests (the golden cases, the scripted adversaries and the judge, with no browser)
===============================================================================
testbed/run_diagnose.py runs real failures in a browser; what it decides without one is tested here: the golden file is well formed
and every case names a variant and a test that exist, the earlier heals are applied to the test source the way a client leaves it,
the adversaries say what they are meant to say, and the judge counts a wrong cause kept and an edit on a cause that may not carry one.

Author: PMAC
===============================================================================
"""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TESTBED = Path(__file__).parents[2] / "testbed"
_SPEC = importlib.util.spec_from_file_location("run_diagnose", TESTBED / "run_diagnose.py")
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)
CASES = json.loads((TESTBED / "golden-diagnose.json").read_text(encoding="utf-8"))["cases"]
SUITE = (TESTBED / "tests" / "python" / "test_diagnose.py").read_text(encoding="utf-8")
CAUSES = {"stale_expectation", "app_bug", "timing", "test_data_or_env", "earlier_heal_wrong_element", "flow_changed", "unknown"}


def case(name):
    return next(c for c in CASES if c["id"] == name)


def test_the_golden_cases_are_well_formed_and_name_real_variants_and_tests():
    assert len({c["id"] for c in CASES}) == len(CASES) >= 9
    for c in CASES:
        assert (TESTBED / "site" / c["variant"] / "index.html").is_file() and f"def {c['test']}(" in SUITE, c["id"]
        exp = c["expect"]
        assert (exp.get("cause") in CAUSES) != (exp.get("status") == "not_applicable"), c["id"]
        if "cause" in exp:
            assert (exp["revert_idx"] is not None) == (exp["cause"] == "earlier_heal_wrong_element"), c["id"]
            assert exp["revert_idx"] is None or exp["revert_idx"] < len(c["prior_heals"])
            if exp["patch"]:
                assert exp["cause"] in bench.PATCH_CAUSES and SUITE.count(exp["patch"]["from"]) == 1, c["id"]
    assert {c["expect"].get("cause") for c in CASES} >= CAUSES - {"unknown"}


def test_an_applied_heal_is_in_the_source_and_a_reverted_or_foreign_one_is_not():
    original = SUITE
    wrong = bench.prepare(case("earlier_heal_chose_the_other_button"), original, "test_diagnose.py")
    h = wrong["prior_heals"][0]
    assert h["to"] in wrong["source"].split("\n")[h["line"] - 1] and wrong["source"].count("\n") == original.count("\n")
    assert h["heal_id"].startswith("h_") and len(h["heal_id"]) == 26
    reverted = bench.prepare(case("reverted_heal_is_no_cause"), original, "test_diagnose.py")
    assert reverted["source"] == original and reverted["prior_heals"][0]["file"] == "test_diagnose.py"
    foreign = bench.prepare(case("heal_elsewhere_is_no_cause"), original, "test_diagnose.py")
    assert foreign["source"] == original and foreign["prior_heals"][0]["file"] == "pages/profile_page.py"


def test_the_adversaries_always_claim_a_wrong_heal_and_the_oracle_follows_the_key():
    wrong = case("earlier_heal_chose_the_other_button")
    cap = {"prepared_priors": [{"heal_id": "h_" + "1" * 24}], "source": SUITE, "line": 12, "file": "test_diagnose.py"}
    for script, named in (("real-id", "h_" + "1" * 24), ("made-up-id", bench.MADE_UP), ("empty-priors", "h_" + "1" * 24)):
        reply = json.loads(bench.script_reply(script, case("app_bug_dead_button"), {**cap, "prepared_priors": []}))
        assert reply["cause"] == "earlier_heal_wrong_element" and reply["heal_id"] in (bench.MADE_UP, named)
    assert json.loads(bench.script_reply("real-id", wrong, cap))["heal_id"] == "h_" + "1" * 24
    oracle = json.loads(bench.script_reply("oracle", wrong, cap))
    assert oracle["cause"] == "earlier_heal_wrong_element" and oracle["heal_id"] == "h_" + "1" * 24 and oracle["patch"] is None
    stale = json.loads(bench.script_reply("oracle", case("stale_expectation_reworded"), cap))
    assert stale["patch"]["from"] in SUITE and stale["patch"]["line"] == SUITE[:SUITE.index(stale["patch"]["from"])].count("\n") + 1
    always = json.loads(bench.script_reply("patch-always", case("app_bug_dead_button"), cap))
    assert always["cause"] == "app_bug" and always["patch"] is not None
    assert bench.script_reply("oracle", case("locator_gone_is_a_heal"), cap) is not None      # never asked: the service makes no call


def answer(cause, revert=None, patch=None, status="diagnosed", calls=1):
    return {"status": status, "usage": {"calls": calls}, "diagnosis": {
        "cause": cause, "revert_heal_id": revert, "suggested_patch": patch} if cause else None}


def test_the_judge_counts_a_wrong_cause_kept_an_edit_on_a_non_edit_cause_and_an_equal_answer():
    priors = [{"heal_id": "h_a"}, {"heal_id": "h_b"}]
    heal, bug = case("earlier_heal_chose_the_other_button"), case("app_bug_dead_button")
    assert bench.judge(heal, answer("earlier_heal_wrong_element", "h_a"), priors)["equal"]
    assert bench.judge(heal, answer("earlier_heal_wrong_element", "h_b"), priors)["wrong_cause_kept"]
    assert bench.judge(bug, answer("earlier_heal_wrong_element", "h_a"), priors)["wrong_cause_kept"]
    assert not bench.judge(heal, answer("unknown"), priors)["equal"] and not bench.judge(heal, answer("unknown"), priors)["wrong_cause_kept"]
    assert bench.judge(bug, answer("app_bug", patch={"from": "a", "to": "b"}), priors)["bad_edit_kept"]
    stale = case("stale_expectation_reworded")["expect"]["patch"]
    assert bench.judge(case("stale_expectation_reworded"), answer("stale_expectation", patch=stale), priors)["equal"]
    assert not bench.judge(case("stale_expectation_reworded"), answer("stale_expectation"), priors)["equal"]
    gone = case("locator_gone_is_a_heal")
    assert bench.judge(gone, answer(None, status="not_applicable", calls=0), priors)["equal"]
    assert not bench.judge(gone, answer(None, status="not_applicable", calls=1), priors)["equal"]


def test_the_cases_the_bench_does_not_build_are_listed_not_faked():
    gaps = json.loads((TESTBED / "diagnose-gaps.json").read_text(encoding="utf-8"))["diagnose"]
    assert gaps and all(g["id"] and g["note"] for g in gaps) and not {g["id"] for g in gaps} & {c["id"] for c in CASES}
