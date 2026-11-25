"""
===============================================================================
Bench end to end (gated on Playwright and a chromium build): two scripted arms through the real acceptance run
===============================================================================
Proves testbed/bench.py from the command line to the report with no model endpoint at all: the scripted ceiling against a scripted
arm whose pick list holds one line (a candidate cap of 1), on two variants of the drift spec with fingerprints off so that every
heal reaches the pick. The ceiling heals the golden element; the capped arm cannot see it, so the two disagree on a step, and the
report has to show that step, the settings that differ, a null cost, and no wrong heal kept by either arm. The two arms run at the
same time (`--parallel-arms`), which also proves their output directories and servers do not collide.

Author: PMAC
===============================================================================
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

TESTBED = Path(__file__).resolve().parents[2] / "testbed"


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    with sync_api.sync_playwright() as p:
        try:
            p.chromium.launch().close()
        except Exception as e:
            pytest.skip(f"chromium is not available: {type(e).__name__}")


@pytest.fixture(scope="module")
def bench_run(chromium, tmp_path_factory):
    sys.path.insert(0, str(TESTBED))
    spec = importlib.util.spec_from_file_location("bench_e2e", TESTBED / "bench.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    out = tmp_path_factory.mktemp("bench")
    status = bench.main(["--arm", "name=lowcap,kind=fake,candidate_cap=1", "--spec", "drift", "--variants", "drift-noid", "drift-gone",
                         "--fingerprints", "off", "--parallel-arms", "--out", str(out)])
    return status, out, json.loads((out / "bench.json").read_text(encoding="utf-8")), (out / "bench.md").read_text(encoding="utf-8")


def arm(report, name):
    return next(a for a in report["arms"] if a["name"] == name)


def run_logs(out, lines=40):
    """The tail of each arm's run log, for a failure message: an arm that healed nothing says why there, not in the report."""
    tails = []
    for log in sorted(out.glob("*/run1/run.log")):
        text = log.read_text(encoding="utf-8", errors="replace").splitlines()
        tails.append(f"--- {log.parent.parent.name}/run1/run.log\n" + "\n".join(text[-lines:]))
    return "\n".join(tails) or "no run logs were written"


def test_the_bench_finishes_and_writes_both_reports_and_each_runs_files(bench_run):
    status, out, report, md = bench_run
    assert status == 0
    for name in ("fake", "lowcap"):
        assert (out / name / "run1" / "acceptance.json").exists() and (out / name / "run1" / "run.log").exists()
    assert [a["name"] for a in report["arms"]] == ["fake", "lowcap"] and md.startswith("# Model bench")


def test_the_header_names_the_date_version_commit_specs_and_client(bench_run):
    _, _, report, md = bench_run
    h = report["header"]
    assert h["specs"] == ["drift"] and h["client"] == "python" and h["version"] and h["commit"] and h["date"].endswith("Z")
    assert h["version"] in md.splitlines()[2] and h["commit"].split()[0] in md.splitlines()[2]


def test_the_ceiling_heals_more_than_the_capped_arm_and_neither_keeps_a_wrong_heal(bench_run):
    _, out, report, _ = bench_run
    fake, low = arm(report, "fake")["summary"], arm(report, "lowcap")["summary"]
    assert fake["healed"]["mean"] > low["healed"]["mean"], run_logs(out)
    assert fake["wrong_kept"]["mean"] == 0 and low["wrong_kept"]["mean"] == 0
    assert fake["steps_run"]["mean"] == low["steps_run"]["mean"] > 0
    assert fake["cost"] is None and low["cost"] is None                   # a scripted model has no price


def test_the_arms_disagree_on_a_step_the_report_lists_with_each_pick_against_the_golden_file(bench_run):
    _, out, report, md = bench_run
    steps = report["agreement"]["differ"]
    assert steps, "the capped arm should miss a step the ceiling heals\n" + run_logs(out)
    step = steps[0]
    assert step["picks"]["fake"]["verdict"] == "ok" and step["picks"]["lowcap"]["verdict"] == "miss"
    assert step["picks"]["lowcap"]["label"].startswith("needs_human") and step["golden"]
    assert "Where the arms differ" in md and "[MISS]" in md


def test_the_settings_every_arm_shared_and_the_one_that_differs_are_printed(bench_run):
    _, _, report, md = bench_run
    assert report["shared_settings"]["candidate_cap"] == 30 and report["shared_settings"]["fingerprints"] == "off"
    assert arm(report, "lowcap")["settings"] == {"candidate_cap": 1}
    assert "Settings that differ between arms: lowcap: candidate cap 1" in md


def test_the_services_own_outcomes_by_model_are_in_each_runs_acceptance_file(bench_run):
    _, out, _, _ = bench_run
    acceptance = json.loads((out / "fake" / "run1" / "acceptance.json").read_text(encoding="utf-8"))
    rows = acceptance["service_metrics"]["outcomes_by_model"]
    assert [r["model"] for r in rows] == ["scripted-fake"] and rows[0]["heals"] > 0 and rows[0]["cost"] is None, run_logs(out)
    assert all(r["duration_ms"] is not None and "golden_target" in r for v in acceptance["variants"] for r in v["rows"])


# ---- model arms against a local stand-in endpoint --------------------------------------------------------------------------
# Not a model: a loopback server that answers every request with "NONE" and records what it was sent. It proves what a real arm's
# run is made of: each arm's own key, model and extra body reach its endpoint, the shared settings are the same in both requests,
# and a price turns the counted tokens into a cost.
@pytest.fixture(scope="module")
def stand_in():
    import http.server
    import threading
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
            data = json.dumps({"choices": [{"message": {"content": "NONE"}}],
                               "usage": {"prompt_tokens": 1000, "completion_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", seen
    server.shutdown()


@pytest.fixture(scope="module")
def endpoint_run(chromium, stand_in, tmp_path_factory):
    url, seen = stand_in
    mp = pytest.MonkeyPatch()
    mp.setenv("SMALL_KEY", "small-key-value")
    mp.setenv("BIG_KEY", "big-key-value")
    mp.setenv("HEAL_TEXT_MODEL", "caller-model-that-must-not-leak")
    try:
        sys.path.insert(0, str(TESTBED))
        spec = importlib.util.spec_from_file_location("bench_e2e_endpoints", TESTBED / "bench.py")
        bench = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bench)
        out = tmp_path_factory.mktemp("bench-endpoints")
        status = bench.main([
            "--arm", f"name=small,base_url={url},model=small-7b,api_key_env=SMALL_KEY,price_input=1,price_output=2",
            "--arm", json.dumps({"name": "big", "base_url": url, "model": "big-70b", "api_key_env": "BIG_KEY",
                                 "extra_body": {"temperature": 0.25, "metadata": {"owner": "extra-body-secret-value"}},
                                 "price_input": 10, "price_output": 20}),
            "--spec", "drift", "--variants", "drift-gone", "--fingerprints", "off", "--max-tokens", "33", "--out", str(out)])
    finally:
        mp.undo()
    return status, json.loads((out / "bench.json").read_text(encoding="utf-8")), (out / "bench.md").read_text(encoding="utf-8"), list(seen), out


def test_each_arm_reaches_its_endpoint_with_its_own_key_model_and_body_and_the_shared_token_limit(endpoint_run):
    status, _, _, seen, _ = endpoint_run
    assert status == 0
    by_model = {r["body"]["model"]: r for r in seen}
    assert set(by_model) == {"small-7b", "big-70b"}                       # the caller's HEAL_TEXT_MODEL reached neither arm
    assert by_model["small-7b"]["auth"] == "Bearer small-key-value" and by_model["big-70b"]["auth"] == "Bearer big-key-value"
    assert by_model["big-70b"]["body"]["temperature"] == 0.25 and by_model["small-7b"]["body"].get("temperature") != 0.25
    assert by_model["small-7b"]["body"]["max_tokens"] == by_model["big-70b"]["body"]["max_tokens"] == 33


def test_the_report_prices_each_arm_from_its_own_tokens_and_never_holds_a_key_or_an_extra_body(endpoint_run):
    _, report, md, _, out = endpoint_run
    small = next(a for a in report["arms"] if a["name"] == "small")["summary"]
    big = next(a for a in report["arms"] if a["name"] == "big")["summary"]
    assert small["input_tokens"]["mean"] > 0 and small["cost"]["mean"] == pytest.approx(
        (small["input_tokens"]["mean"] * 1 + small["output_tokens"]["mean"] * 2) / 1e6)
    assert big["cost"]["mean"] == pytest.approx((big["input_tokens"]["mean"] * 10 + big["output_tokens"]["mean"] * 20) / 1e6)
    assert report["shared_settings"]["max_tokens"] == 33
    text = md + json.dumps(report) + "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.rglob("*.log"))
    for secret in ("small-key-value", "big-key-value", "extra-body-secret-value"):
        assert secret not in text, secret
    assert "temperature, metadata" in md or "metadata, temperature" in md      # the names of the extra body's keys are reported
    assert "SMALL_KEY" in md                                              # the variable's name is reported, never its value
