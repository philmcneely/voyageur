"""
===============================================================================
Bench report tests (the model comparison built from acceptance.json files, with no browser and no model)
===============================================================================
testbed/bench.py runs the acceptance run once per arm and repeat; testbed/bench_report.py turns those acceptance.json files into the
comparison. These tests feed it fixture files (two arms, repeats, a disagreement) and pin what it reports: the per-arm numbers and
their spread over repeats, the steps where the arms differ and whether each pick matches the golden file, the fit lines, and the
header and settings that make the comparison fair to read.

Author: PMAC
===============================================================================
"""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SPEC = importlib.util.spec_from_file_location("bench_report", Path(__file__).parents[2] / "testbed" / "bench_report.py")
report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(report)

TEST_ID = "page.get_by_test_id('name')"
ROLE = "page.get_by_role('button', name='Save')"


def row(test, line, index, expected, status, reason=None, locator=None, on_target=None, golden=None, calls=0, tin=0, tout=0,
        caught=0, ms=100):
    return {"test": test, "line": line, "step_index": index, "statement": f"{test} line {line}", "expected": {"outcome": expected},
            "status": status, "reason": reason, "final_locator": locator, "on_target": on_target, "golden_target": golden,
            "calls": calls, "input_tokens": tin, "output_tokens": tout, "wrong_catches": caught, "duration_ms": ms, "met": True}


def variant(name, rows, spec="drift", expected=None):
    healed = [r for r in rows if r["status"] == "healed"]
    return {"spec": spec, "variant": name, "rows": rows, "summary": {
        "steps_expected": expected if expected is not None else len(rows), "steps_run": len(rows), "healed": len(healed),
        "needs_human": sum(r["status"] == "needs_human" for r in rows), "model_calls": sum(r["calls"] for r in rows),
        "input_tokens": sum(r["input_tokens"] for r in rows), "output_tokens": sum(r["output_tokens"] for r in rows),
        "wrong_catches": sum(r["wrong_catches"] for r in rows), "wrong_kept": sum(1 for r in healed if not r["on_target"])}}


def run(variants, cost=None, model_ms=(5.0, 9.0), client="python"):
    return {"client": client, "model": "env", "service": "in-process", "timeout_ms": 1500, "variants": variants,
            "service_metrics": {"cost": cost, "latency": {"model_ms_p50": model_ms[0], "model_ms_p95": model_ms[1]}}}


def ceiling_run():
    """The scripted arm: every step healed on the golden element, one step is gone (needs_human, as the golden file says)."""
    return run([variant("v1", [row("t1", 10, 0, "healed", "healed", locator=TEST_ID, on_target=True, golden=TEST_ID, calls=1, tin=300, tout=3),
                               row("t1", 14, 1, "healed", "healed", locator=ROLE, on_target=True, golden=ROLE, calls=1, tin=300, tout=3),
                               row("t2", 9, 2, "needs_human", "needs_human", reason="target_missing", golden=None, calls=1, tin=300, tout=3)])])


def cheap_run(second_pick=ROLE, second_status="healed", on_target=True, cost=0.002, caught=0, ms=400, reason=None, third="needs_human"):
    third_row = (row("t2", 9, 2, "needs_human", "needs_human", reason="target_missing", golden=None, calls=1, tin=310, tout=5)
                 if third == "needs_human" else
                 row("t2", 9, 2, "needs_human", "healed", locator="page.locator('.x')", on_target=False, golden=None, calls=1, tin=310, tout=5))
    return run([variant("v1", [row("t1", 10, 0, "healed", "healed", locator=TEST_ID, on_target=True, golden=TEST_ID, calls=1, tin=310, tout=5, ms=ms),
                               row("t1", 14, 1, "healed", second_status, reason=reason, locator=second_pick if second_status == "healed" else None,
                                   on_target=on_target if second_status == "healed" else None, golden=ROLE, calls=2, tin=620, tout=9,
                                   caught=caught, ms=ms * 2),
                               third_row])], cost=cost, model_ms=(300.0, 700.0))


def arm(name, runs, kind="endpoint", **settings):
    return {"name": name, "kind": kind, "model": settings.pop("model", name), "provider": "openai", "base_url": "http://localhost:8000/v1",
            "api_key_env": None, "settings": settings, "runs": runs}


HEADER = {"date": "2026-11-25T10:00:00Z", "version": "0.1.0", "commit": "abc1234", "specs": ["drift"], "client": "python", "repeat": 1}
SHARED = {"max_tokens": 64, "candidate_cap": 30, "max_calls": 2, "timeout_s": 120, "action_timeout_ms": 1500, "thinking": "off",
          "fingerprints": "on", "vision": "off"}


def build(arms, header=None, shared=None):
    return report.build(header or HEADER, shared or SHARED, arms)


# ---- one run --------------------------------------------------------------------------------------------------------------
def test_a_run_is_summed_over_its_variants_the_way_the_acceptance_report_defines_each_number():
    s = report.run_summary(cheap_run(second_status="needs_human", reason="no_pick", caught=1))
    assert (s["healed"], s["needs_human"], s["not_drift"]) == (1, 2, 0)
    assert s["needs_human_by_reason"] == {"no_pick": 1, "target_missing": 1}
    assert (s["steps_run"], s["steps_golden"], s["wrong_kept"], s["wrong_caught"]) == (3, 3, 0, 1)
    assert (s["model_calls"], s["input_tokens"], s["output_tokens"]) == (4, 1240, 19)
    assert s["cost"] == 0.002 and s["model_ms_p50"] == 300.0 and s["model_ms_p95"] == 700.0


def test_steps_are_counted_against_the_golden_file_and_a_wrong_kept_heal_is_counted_as_one():
    s = report.run_summary(cheap_run(on_target=False, third="healed"))
    assert s["wrong_kept"] == 2 and s["healed"] == 3
    assert s["steps_matching_golden"] == 1      # only the first step is a healed step on the golden element or a needs_human the golden wants


def test_heal_latency_percentiles_come_from_the_recorded_durations_and_cost_is_null_when_unpriced():
    s = report.run_summary(cheap_run(cost=None, ms=1000))
    assert s["cost"] is None and s["heal_ms_p50"] == 1000 and s["heal_ms_p95"] == 2000


def test_a_run_from_a_service_without_metrics_has_null_cost_and_model_latency():
    bare = cheap_run()
    del bare["service_metrics"]
    s = report.run_summary(bare)
    assert s["cost"] is None and s["model_ms_p50"] is None


def test_a_scripted_run_reports_no_cost():
    s = report.run_summary(ceiling_run())
    assert s["cost"] is None and s["healed"] == 2 and s["wrong_kept"] == 0 and s["steps_matching_golden"] == 3


# ---- repeats --------------------------------------------------------------------------------------------------------------
def test_repeats_give_min_max_and_mean_for_every_number():
    a = arm("cheap", [cheap_run(), cheap_run(second_status="needs_human", reason="no_pick"), cheap_run(second_status="needs_human", reason="no_pick")])
    s = report.arm_summary(a)
    assert s["repeats"] == 3
    assert s["healed"] == {"min": 1, "max": 2, "mean": pytest.approx(4 / 3), "values": [2, 1, 1]}
    assert s["needs_human_by_reason"]["no_pick"] == {"min": 0, "max": 1, "mean": pytest.approx(2 / 3), "values": [0, 1, 1]}
    assert s["needs_human_by_reason"]["target_missing"]["min"] == 1
    assert s["cost"]["mean"] == pytest.approx(0.002)


def test_a_cost_that_is_null_in_any_repeat_is_null_for_the_arm():
    a = arm("cheap", [cheap_run(cost=0.002), cheap_run(cost=None)])
    assert report.arm_summary(a)["cost"] is None


def test_one_repeat_has_equal_min_max_and_mean():
    s = report.arm_summary(arm("cheap", [cheap_run()]))
    assert s["healed"]["min"] == s["healed"]["max"] == s["healed"]["mean"] == 2


# ---- the agreement view ---------------------------------------------------------------------------------------------------
def two_arms():
    return [arm("fake", [ceiling_run()], kind="fake"),
            arm("cheap", [cheap_run(second_pick="page.locator('.btn')", on_target=False)])]


def test_the_agreement_view_lists_only_the_steps_where_the_arms_differ():
    agree = build(two_arms())["agreement"]
    assert agree["steps_total"] == 3 and len(agree["differ"]) == 1
    step = agree["differ"][0]
    assert step["variant"] == "v1" and step["test"] == "t1" and step["line"] == 14
    assert step["golden"] == ROLE
    assert step["picks"]["fake"]["label"] == ROLE and step["picks"]["fake"]["matches_golden"] is True
    assert step["picks"]["cheap"]["label"] == "page.locator('.btn')" and step["picks"]["cheap"]["matches_golden"] is False


def test_a_needs_human_step_shows_its_reason_and_matches_when_the_golden_file_says_the_element_is_gone():
    arms = [arm("fake", [ceiling_run()], kind="fake"), arm("cheap", [cheap_run(second_status="needs_human", reason="no_pick")])]
    step = build(arms)["agreement"]["differ"][0]
    assert step["picks"]["cheap"]["label"] == "needs_human: no_pick" and step["picks"]["cheap"]["matches_golden"] is False


def test_arms_that_agree_everywhere_leave_the_view_empty_and_the_markdown_says_so():
    arms = [arm("fake", [ceiling_run()], kind="fake"), arm("same", [ceiling_run()])]
    bench = build(arms)
    assert bench["agreement"]["differ"] == [] and "agree on every step" in report.markdown(bench)


def test_a_step_one_arm_never_reached_is_listed_as_not_reached():
    short = cheap_run()
    short["variants"][0]["rows"] = short["variants"][0]["rows"][:1]
    step = build([arm("fake", [ceiling_run()], kind="fake"), arm("short", [short])])["agreement"]["differ"][0]
    assert step["picks"]["short"]["label"] == "not reached" and step["picks"]["short"]["matches_golden"] is None


def test_repeats_that_disagree_show_each_pick_with_how_often_it_came():
    a = arm("cheap", [cheap_run(), cheap_run(second_pick="page.locator('.btn')", on_target=False), cheap_run()])
    step = build([arm("fake", [ceiling_run()], kind="fake"), a])["agreement"]["differ"][0]
    cell = step["picks"]["cheap"]
    assert cell["label"] == ROLE and cell["votes"] == {ROLE: 2, "page.locator('.btn')": 1}
    assert cell["matches_golden"] is True and cell["stable"] is False


def test_a_step_where_repeats_wobble_is_listed_even_when_every_arm_has_the_same_majority_pick():
    a = arm("cheap", [cheap_run(), cheap_run(second_pick="page.locator('.btn')", on_target=False), cheap_run()])
    arms = [arm("fake", [ceiling_run()], kind="fake"), a]
    assert [s["line"] for s in build(arms)["agreement"]["differ"]] == [14]


# ---- the fit lines --------------------------------------------------------------------------------------------------------
def test_each_arm_gets_a_fit_line_of_facts_and_the_ceiling_gets_none():
    bench = build(two_arms())
    assert [f["arm"] for f in bench["fit"]] == ["cheap"]
    line = bench["fit"][0]["line"]
    assert "wrong heals kept: 1" in line and "healed 2 of the 2 the scripted ceiling healed" in line and "100%" in line


def test_the_fit_line_uses_the_mean_over_repeats_and_names_the_repeat_count():
    a = arm("cheap", [cheap_run(), cheap_run(second_status="needs_human", reason="no_pick")])
    line = build([arm("fake", [ceiling_run()], kind="fake"), a])["fit"][0]["line"]
    assert "healed 1.5 of the 2" in line and "75%" in line and "mean of 2 repeats" in line


def test_the_share_of_the_ceiling_is_null_when_the_ceiling_healed_nothing():
    nothing = run([variant("v1", [row("t", 1, 0, "needs_human", "needs_human", reason="target_missing", golden=None)])])
    bench = build([arm("fake", [nothing], kind="fake"), arm("cheap", [cheap_run()])])
    assert bench["fit"][0]["share_of_ceiling"] is None and "n/a" in bench["fit"][0]["line"]


def test_the_fit_lines_state_no_recommendation():
    text = report.markdown(build(two_arms())).lower()
    for word in ("recommend", "should use", "best model", "worth it", "we suggest", "prefer"):
        assert word not in text


# ---- the document ---------------------------------------------------------------------------------------------------------
def test_the_header_line_has_date_version_commit_specs_and_client():
    md = report.markdown(build(two_arms()))
    first = md.splitlines()[2]
    for part in ("2026-11-25T10:00:00Z", "0.1.0", "abc1234", "drift", "python"):
        assert part in first


def test_the_settings_every_arm_shared_are_printed_so_the_comparison_can_be_checked_for_fairness():
    md = report.markdown(build(two_arms()))
    for part in ("max tokens", "64", "candidate cap", "30", "max calls", "2", "timeout", "120", "1500"):
        assert part in md
    assert "same for every arm" in md.lower()


def test_a_setting_that_differs_between_arms_is_called_out():
    arms = [arm("fake", [ceiling_run()], kind="fake"), arm("low", [ceiling_run()], kind="fake", candidate_cap=8)]
    md = report.markdown(build(arms))
    assert "settings that differ between arms" in md.lower() and "low: candidate cap 8" in md


def test_the_table_has_the_scripted_ceiling_first_and_one_column_per_arm():
    md = report.markdown(build(two_arms()))
    head = next(line for line in md.splitlines() if line.startswith("| metric"))
    assert head.index("fake") < head.index("cheap")
    for label in ("healed", "needs_human: target_missing", "wrong heals kept", "wrong element caught", "steps run / golden", "model calls",
                  "input tokens", "output tokens", "cost", "heal latency p50", "heal latency p95"):
        assert label in md


def test_an_unpriced_arm_shows_n_a_for_cost_and_a_priced_one_shows_the_number():
    md = report.markdown(build(two_arms()))
    cost_line = next(line for line in md.splitlines() if line.startswith("| cost"))
    assert "n/a" in cost_line and "0.002" in cost_line


def test_repeats_show_min_max_and_mean_in_the_table():
    a = arm("cheap", [cheap_run(), cheap_run(second_status="needs_human", reason="no_pick")])
    md = report.markdown(build([arm("fake", [ceiling_run()], kind="fake"), a], header=dict(HEADER, repeat=2)))
    healed = next(line for line in md.splitlines() if line.startswith("| healed"))
    assert "1 to 2 (mean 1.5)" in healed


def test_cells_with_a_pipe_or_a_newline_cannot_break_the_table():
    odd = cheap_run(second_pick="page.locator('a|b')\nx", on_target=False)
    md = report.markdown(build([arm("fake", [ceiling_run()], kind="fake"), arm("cheap", [odd])]))
    assert "a\\|b" in md and "\nx" not in md


def test_the_json_is_plain_data_and_never_holds_a_key():
    a = arm("cheap", [cheap_run()])
    a["api_key_env"] = "CHEAP_KEY"
    bench = build([arm("fake", [ceiling_run()], kind="fake"), a])
    text = json.dumps(bench)
    assert json.loads(text)["arms"][1]["name"] == "cheap" and "CHEAP_KEY" in text
    assert [x["name"] for x in bench["arms"]] == ["fake", "cheap"]


def test_an_arm_whose_runs_failed_is_reported_as_failed_not_dropped():
    broken = arm("cheap", [])
    broken["error"] = "the run produced no acceptance.json (exit 2)"
    bench = build([arm("fake", [ceiling_run()], kind="fake"), broken])
    md = report.markdown(bench)
    assert "cheap" in md and "no acceptance.json" in md
    assert bench["fit"][0]["line"].startswith("cheap: no result")


def test_a_step_without_a_heal_is_a_miss_and_a_heal_on_another_element_or_where_the_element_is_gone_is_wrong():
    miss = build([arm("fake", [ceiling_run()], kind="fake"), arm("cheap", [cheap_run(second_status="needs_human", reason="no_pick")])])
    assert miss["agreement"]["differ"][0]["picks"]["cheap"]["verdict"] == "miss"
    wrong = build([arm("fake", [ceiling_run()], kind="fake"), arm("cheap", [cheap_run(second_pick="page.locator('.btn')", on_target=False)])])
    assert wrong["agreement"]["differ"][0]["picks"]["cheap"]["verdict"] == "wrong"
    gone = build([arm("fake", [ceiling_run()], kind="fake"), arm("cheap", [cheap_run(third="healed")])])["agreement"]["differ"]
    assert [s["picks"]["cheap"]["verdict"] for s in gone if s["line"] == 9] == ["wrong"]
    md = report.markdown(build([arm("fake", [ceiling_run()], kind="fake"), arm("cheap", [cheap_run(second_status="needs_human", reason="no_pick")])]))
    assert "[MISS]" in md and "[ok]" in md


def test_text_from_a_page_or_a_model_cannot_form_a_link_an_image_or_a_tag_in_the_markdown():
    hostile = "![x](https://example.invalid/p.png) <img src=x onerror=1> `tick` [a](b)"
    md = report.markdown(build([arm("fake", [ceiling_run()], kind="fake"), arm("cheap", [cheap_run(second_pick=hostile, on_target=False)])]))
    assert "<img" not in md and "![x](" not in md and "](https://example.invalid" not in md.replace("\\]", "")
    assert "&lt;img src=x onerror=1&gt;" in md and "\\[x\\]" in md


def test_the_arms_table_names_the_keys_of_an_extra_body_and_never_values():
    a = arm("cheap", [cheap_run()])
    a["extra_body_keys"] = ["temperature", "top_p"]
    md = report.markdown(build([arm("fake", [ceiling_run()], kind="fake"), a]))
    assert "extra body keys" in md and "temperature, top_p" in md


def test_an_arm_that_stopped_after_some_repeats_keeps_what_it_finished_and_says_so():
    partial = arm("cheap", [cheap_run()])
    partial["error"] = "the run of cheap passed 7200 s and was stopped"
    bench = build([arm("fake", [ceiling_run()], kind="fake"), partial])
    assert "stopped early" in bench["fit"][0]["line"] and "passed 7200 s" in report.markdown(bench)
