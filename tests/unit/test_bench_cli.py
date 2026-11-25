"""
===============================================================================
Bench runner tests (arms, the environment each arm runs with, the order arms run in; no browser, no model)
===============================================================================
testbed/bench.py turns `--arm` flags or an arms file into arms, gives every arm the same shared settings plus its own model
settings, runs the acceptance run once per arm and repeat, and writes the report. These tests pin the parts that decide whether the
comparison is fair and safe: what an arm may set, what the child process inherits (nothing from the caller's own HEAL_* variables),
where the API key goes (the child's environment, never its command line or the report), which arms run once, and what runs in
parallel. The run itself is proved in tests/browser/test_bench_e2e.py with two scripted arms.

Author: PMAC
===============================================================================
"""
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TESTBED = Path(__file__).parents[2] / "testbed"
sys.path.insert(0, str(TESTBED))
_SPEC = importlib.util.spec_from_file_location("bench", TESTBED / "bench.py")
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)

CHEAP = "name=cheap,base_url=http://localhost:8000/v1,model=small-7b,provider=openai,api_key_env=CHEAP_KEY"


def shared(**over):
    return bench.shared_settings(bench.parse_args(["--arm", CHEAP] + [f"--{k.replace('_', '-')}={v}" for k, v in over.items()]))


# ---- arms --------------------------------------------------------------------------------------------------------------------
def test_an_arm_flag_is_a_name_and_the_settings_of_one_model():
    arm = bench.parse_arm(CHEAP)
    assert (arm.name, arm.kind, arm.model, arm.provider, arm.base_url, arm.api_key_env) == (
        "cheap", "endpoint", "small-7b", "openai", "http://localhost:8000/v1", "CHEAP_KEY")


def test_an_arm_can_be_a_json_object_which_is_how_an_extra_body_is_given():
    arm = bench.parse_arm(json.dumps({"name": "x", "base_url": "http://localhost:1/v1", "model": "m",
                                      "extra_body": {"temperature": 0}, "price_input": 0.1, "price_output": 0.4}))
    assert arm.extra_body == {"temperature": 0} and arm.price == (0.1, 0.4) and arm.provider == "openai"


def test_arms_come_from_a_json_or_toml_file(tmp_path):
    (tmp_path / "arms.json").write_text(json.dumps({"arms": [{"name": "a", "base_url": "http://localhost:1/v1", "model": "m"}]}))
    (tmp_path / "arms.toml").write_text('[[arms]]\nname = "b"\nbase_url = "http://localhost:2/v1"\nmodel = "n"\n'
                                        'extra_body = { top_p = 0.9 }\n')
    assert [a.name for a in bench.load_arms_file(tmp_path / "arms.json")] == ["a"]
    b = bench.load_arms_file(tmp_path / "arms.toml")[0]
    assert b.name == "b" and b.extra_body == {"top_p": 0.9}


@pytest.mark.parametrize("text", [
    "name=x,model=m",                                                      # no base_url
    "name=x,base_url=http://localhost:1/v1",                               # no model
    "base_url=http://localhost:1/v1,model=m",                              # no name
    "name=x,base_url=http://localhost:1/v1,model=m,surprise=1",            # unknown key
    "name=fake,base_url=http://localhost:1/v1,model=m",                    # the ceiling's name
    "name=has space,base_url=http://localhost:1/v1,model=m",
    "name=x,base_url=ftp://localhost/v1,model=m",
    "name=x,base_url=http://user:pw@localhost/v1,model=m",                 # a credential belongs in api_key_env
    "name=x,base_url=http://localhost:1/v1,model=m,provider=other",
    "name=x,base_url=http://localhost:1/v1,model=m,candidate_cap=3",       # only a scripted arm may set the pipeline's own knobs
    "name=x,kind=fake,model=m",                                            # a scripted arm names no endpoint
    "name=x,base_url=http://localhost:1/v1,model=m,price_input=1",         # half a price
])
def test_a_malformed_arm_is_refused(text):
    with pytest.raises(bench.BenchError):
        bench.parse_arm(text)


def test_an_api_key_pasted_where_the_variable_name_goes_is_refused_without_echoing_it():
    secret = "sk-" + "A1b2C3d4" * 4
    with pytest.raises(bench.BenchError) as e:
        bench.parse_arm(f"name=x,base_url=http://localhost:1/v1,model=m,api_key_env={secret}")
    assert secret not in str(e.value) and "environment variable" in str(e.value)


def test_arm_names_must_be_unique():
    with pytest.raises(bench.BenchError):
        bench.arms_from([bench.parse_arm(CHEAP), bench.parse_arm(CHEAP)])


def test_the_scripted_ceiling_is_always_first_and_a_bench_needs_one_more_arm():
    arms = bench.arms_from([bench.parse_arm(CHEAP)])
    assert [a.name for a in arms] == ["fake", "cheap"] and arms[0].kind == "fake"
    with pytest.raises(bench.BenchError):
        bench.arms_from([])


def test_a_scripted_arm_may_set_the_pipelines_own_knobs_which_the_report_then_shows():
    arm = bench.parse_arm("name=low,kind=fake,candidate_cap=8,max_calls=1")
    assert arm.kind == "fake" and arm.overrides == {"candidate_cap": 8, "max_calls": 1}


# ---- the settings every arm shares -----------------------------------------------------------------------------------------
def test_the_shared_settings_are_the_services_own_defaults_unless_given():
    s = shared()
    assert (s["max_tokens"], s["candidate_cap"], s["max_calls"], s["timeout_s"], s["action_timeout_ms"]) == (64, 30, 2, 120, 1500)
    assert s["thinking"] == "off" and s["fingerprints"] == "on" and s["vision"] == "off"


def test_the_shared_settings_can_be_set_once_for_all_arms():
    s = shared(max_tokens=256, candidate_cap=12, max_calls=3, timeout_s=30, action_timeout_ms=3000, thinking="on")
    assert (s["max_tokens"], s["candidate_cap"], s["max_calls"], s["timeout_s"], s["action_timeout_ms"], s["thinking"]) == (
        256, 12, 3, 30, 3000, "on")


# ---- the environment and command of one run -----------------------------------------------------------------------------------
def env_of(arm_text, base=None, **over):
    args = bench.parse_args(["--arm", arm_text] + [f"--{k.replace('_', '-')}={v}" for k, v in over.items()])
    arm = bench.parse_arm(arm_text)
    return bench.arm_env(arm, bench.shared_settings(args), base or {"CHEAP_KEY": "the-key-value", "PATH": "/bin"})


def test_the_arms_model_settings_become_the_text_role_variables_of_the_run():
    env = env_of(CHEAP + ",price_input=0.1,price_output=0.4")
    assert env["HEAL_TEXT_BASE_URL"] == "http://localhost:8000/v1" and env["HEAL_TEXT_MODEL"] == "small-7b"
    assert env["HEAL_TEXT_PROVIDER"] == "openai" and env["HEAL_TEXT_API_KEY"] == "the-key-value"
    assert json.loads(env["HEAL_MODEL_PRICES"]) == {"small-7b": {"input": 0.1, "output": 0.4}}
    assert env["PATH"] == "/bin"


def test_every_arm_gets_the_same_shared_settings_in_its_environment():
    a = env_of(CHEAP, max_tokens=128, candidate_cap=10, max_calls=3, timeout_s=45)
    b = env_of("name=other,base_url=http://localhost:9/v1,model=big,api_key_env=CHEAP_KEY", max_tokens=128, candidate_cap=10,
               max_calls=3, timeout_s=45)
    for key in ("HEAL_TEXT_MAX_TOKENS", "HEAL_CANDIDATE_CAP", "HEAL_MAX_CALLS", "HEAL_TEXT_TIMEOUT_S", "HEAL_TEXT_THINKING"):
        assert a[key] == b[key], key
    assert (a["HEAL_TEXT_MAX_TOKENS"], a["HEAL_CANDIDATE_CAP"], a["HEAL_MAX_CALLS"], a["HEAL_TEXT_TIMEOUT_S"]) == ("128", "10", "3", "45")


def test_the_callers_own_heal_variables_never_reach_an_arm():
    base = {"CHEAP_KEY": "k", "HEAL_TEXT_MODEL": "leak", "HEAL_BASE_URL": "http://elsewhere/v1", "HEAL_MODEL": "leak",
            "HEAL_API_KEY": "leak", "HEAL_EXTRA_BODY": "{}", "HEAL_SERVICE_URL": "http://elsewhere", "HEAL_VISION_MODEL": "leak",
            "HEAL_CANDIDATE_CAP": "1", "HEAL_RUN_TOKEN_BUDGET": "5", "HEAL_DB": "x.db", "KEEP": "me"}
    env = env_of(CHEAP, base=base)
    assert env["HEAL_TEXT_MODEL"] == "small-7b" and env["KEEP"] == "me"
    for key in ("HEAL_BASE_URL", "HEAL_MODEL", "HEAL_API_KEY", "HEAL_EXTRA_BODY", "HEAL_SERVICE_URL", "HEAL_VISION_MODEL",
                "HEAL_RUN_TOKEN_BUDGET", "HEAL_DB"):
        assert key not in env, key
    assert env["HEAL_CANDIDATE_CAP"] == "30"           # the shared value, not the caller's 1


def test_prices_the_caller_set_for_all_models_are_kept_and_the_arms_own_price_is_added():
    base = {"CHEAP_KEY": "k", "HEAL_MODEL_PRICES": json.dumps({"big": {"input": 3, "output": 15}})}
    prices = json.loads(env_of(CHEAP + ",price_input=0.1,price_output=0.4", base=base)["HEAL_MODEL_PRICES"])
    assert prices == {"big": {"input": 3, "output": 15}, "small-7b": {"input": 0.1, "output": 0.4}}


def test_an_unset_key_variable_is_an_error_before_anything_runs():
    with pytest.raises(bench.BenchError) as e:
        env_of(CHEAP, base={"PATH": "/bin"})
    assert "CHEAP_KEY" in str(e.value)


def test_an_arm_with_no_key_variable_sends_no_key():
    env = env_of("name=local,base_url=http://localhost:8000/v1,model=m", base={"PATH": "/bin", "HEAL_TEXT_API_KEY": "leak"})
    assert "HEAL_TEXT_API_KEY" not in env


def test_the_key_is_in_the_environment_and_never_in_the_command_line():
    args = bench.parse_args(["--arm", CHEAP])
    arm = bench.parse_arm(CHEAP)
    argv = bench.arm_argv(arm, bench.shared_settings(args), args, Path("/out/cheap/run1"))
    assert "the-key-value" not in " ".join(argv) and "CHEAP_KEY" not in " ".join(argv)
    assert argv[argv.index("--model") + 1] == "env" and argv[argv.index("--out") + 1] == "/out/cheap/run1"


def test_a_scripted_arm_runs_the_fake_model_with_its_knobs_on_the_command_line():
    args = bench.parse_args(["--arm", "name=low,kind=fake,candidate_cap=8", "--candidate-cap", "20", "--max-calls", "3"])
    argv = bench.arm_argv(bench.parse_arm("name=low,kind=fake,candidate_cap=8"), bench.shared_settings(args), args, Path("/o"))
    assert argv[argv.index("--model") + 1] == "fake"
    assert argv[argv.index("--candidate-cap") + 1] == "8" and argv[argv.index("--max-calls") + 1] == "3"
    ceiling = bench.arm_argv(bench.arms_from([bench.parse_arm(CHEAP)])[0], bench.shared_settings(args), args, Path("/o"))
    assert ceiling[ceiling.index("--candidate-cap") + 1] == "20"


def test_the_command_carries_the_specs_client_and_timeouts_the_same_for_every_arm():
    args = bench.parse_args(["--arm", CHEAP, "--spec", "drift", "orders", "--variants", "drift-fr", "--client", "ts",
                             "--ts-client-dir", "/ts", "--action-timeout-ms", "2500", "--fingerprints", "off"])
    argv = bench.arm_argv(bench.parse_arm(CHEAP), bench.shared_settings(args), args, Path("/o"))
    assert argv[argv.index("--spec") + 1:argv.index("--spec") + 3] == ["drift", "orders"]
    assert argv[argv.index("--variants") + 1] == "drift-fr" and argv[argv.index("--client") + 1] == "ts"
    assert argv[argv.index("--ts-client-dir") + 1] == "/ts" and argv[argv.index("--timeout-ms") + 1] == "2500"
    assert argv[argv.index("--fingerprints") + 1] == "off"


# ---- running -------------------------------------------------------------------------------------------------------------------
def arms3():
    return bench.arms_from([bench.parse_arm(CHEAP), bench.parse_arm("name=low,kind=fake,candidate_cap=8")])


def test_arms_run_one_after_the_other_by_default_and_each_endpoint_arm_runs_repeat_times_but_a_scripted_arm_once():
    calls = []
    results = bench.run_all(arms3(), repeat=3, parallel=False, runner=lambda arm, n: calls.append((arm.name, n)) or {"n": n})
    assert calls == [("fake", 1), ("cheap", 1), ("cheap", 2), ("cheap", 3), ("low", 1)]
    assert [len(r["runs"]) for r in results] == [1, 3, 1]


def test_parallel_arms_overlap_in_time_and_keep_the_report_order():
    gate = threading.Barrier(3, timeout=5)

    def runner(arm, n):
        gate.wait()                      # all three arms must be running at once to pass
        return {"arm": arm.name}
    results = bench.run_all(arms3(), repeat=1, parallel=True, runner=runner)
    assert [r["arm"].name for r in results] == ["fake", "cheap", "low"]


def test_the_repeats_of_one_arm_never_run_at_the_same_time_even_with_parallel_arms():
    live, worst = [], [0]
    lock = threading.Lock()

    def runner(arm, n):
        with lock:
            live.append(arm.name)
            worst[0] = max(worst[0], live.count("cheap"))
        import time
        time.sleep(0.05)
        with lock:
            live.remove(arm.name)
        return {}
    bench.run_all(arms3(), repeat=3, parallel=True, runner=runner)
    assert worst[0] == 1


def test_an_arm_whose_run_fails_is_recorded_and_the_others_still_run():
    def runner(arm, n):
        if arm.name == "cheap":
            raise bench.BenchError("the run produced no acceptance.json (exit 2)")
        return {"ok": True}
    results = bench.run_all(arms3(), repeat=2, parallel=False, runner=runner)
    cheap = next(r for r in results if r["arm"].name == "cheap")
    assert cheap["runs"] == [] and "no acceptance.json" in cheap["error"]
    assert all(r["runs"] for r in results if r["arm"].name != "cheap")


def test_the_report_holds_neither_an_extra_body_nor_a_key_but_names_the_variable_the_key_came_from():
    args = bench.parse_args(["--arm", CHEAP])
    cheap = bench.parse_arm(json.dumps({"name": "cheap", "base_url": "http://localhost:1/v1", "model": "m", "api_key_env": "CHEAP_KEY",
                                        "extra_body": {"api_key": "body-secret", "seed": 1}}))
    results = [{"arm": a, "runs": [], "error": "x"} for a in bench.arms_from([cheap])]
    text = json.dumps(bench.report_of(args, bench.shared_settings(args), results))
    assert "body-secret" not in text and "CHEAP_KEY" in text
    assert json.loads(text)["arms"][1]["extra_body_keys"] == ["api_key", "seed"]          # the names, so a reader sees what was set


def test_an_arm_run_that_is_not_json_is_an_arm_failure_not_a_crash():
    def runner(arm, n):
        raise json.JSONDecodeError("bad", "x", 0)
    cheap = bench.run_all(arms3(), repeat=1, parallel=False, runner=runner)
    assert all(r["runs"] == [] and "JSONDecodeError" in r["error"] for r in cheap)


# ---- keys, names and runs that go wrong ------------------------------------------------------------------------------------------
def test_a_name_with_a_trailing_newline_or_a_case_twin_is_refused():
    with pytest.raises(bench.BenchError):
        bench.parse_arm(json.dumps({"name": "x\n", "base_url": "http://localhost:1/v1", "model": "m"}))
    with pytest.raises(bench.BenchError):
        bench.arms_from([bench.parse_arm(CHEAP), bench.parse_arm(CHEAP.replace("name=cheap", "name=Cheap"))])


@pytest.mark.parametrize("name", ["KEY", "CHEAPKEY", "cheap_key", "sk-" + "A" * 30, "A" * 40, "CHEAP_KEY\n"])
def test_api_key_env_must_look_like_a_variable_name_with_an_underscore(name):
    with pytest.raises(bench.BenchError):
        bench.parse_arm(json.dumps({"name": "x", "base_url": "http://localhost:1/v1", "model": "m", "api_key_env": name}))


def test_a_key_pasted_into_a_name_model_or_url_is_refused_without_echoing_it(monkeypatch):
    secret = "k" * 12 + "SECRETVALUE"
    monkeypatch.setenv("CHEAP_KEY", secret)
    for field in ("name", "model", "base_url"):
        data = {"name": "x", "base_url": "http://localhost:1/v1", "model": "m", "api_key_env": "CHEAP_KEY"}
        data[field] = secret if field != "base_url" else f"http://localhost:1/{secret}"
        with pytest.raises(bench.BenchError) as e:
            bench.parse_arm(json.dumps(data))
        assert secret not in str(e.value), field
    other = bench.Arm(name=secret, base_url="http://localhost:2/v1", model="m")
    with pytest.raises(bench.BenchError) as e:                                   # the key of another arm
        bench.check_keys_stay_out_of_fields([bench.parse_arm(CHEAP), other], {"CHEAP_KEY": secret})
    assert secret not in str(e.value)


def test_an_unknown_field_name_that_could_be_a_key_is_not_echoed():
    with pytest.raises(bench.BenchError) as e:
        bench.parse_arm("name=x,base_url=http://localhost:1/v1,model=m," + "sk-" + "ABCDEF" * 3 + "=1")
    assert "ABCDEF" not in str(e.value)


def test_a_blank_key_variable_is_an_unset_one():
    with pytest.raises(bench.BenchError):
        env_of(CHEAP, base={"CHEAP_KEY": "   ", "PATH": "/bin"})


def test_the_services_own_rules_for_a_url_and_an_extra_body_are_applied_before_anything_runs():
    for data in ({"base_url": "http://localhost:1/v1\nx"}, {"base_url": "http://localhost:99999/v1"},
                 {"extra_body": {"model": "other"}}, {"extra_body": {"messages": []}}):
        with pytest.raises(bench.BenchError):
            bench.parse_arm(json.dumps({"name": "x", "base_url": "http://localhost:1/v1", "model": "m", **data}))


def runner_for(tmp_path, command, timeout=30):
    args = bench.parse_args(["--arm", CHEAP, "--run-timeout-s", str(timeout)])
    run = bench.make_runner(args, bench.shared_settings(args), tmp_path, {"CHEAP_KEY": "k", "PATH": "/bin"})
    return run, command


def test_an_acceptance_json_left_by_an_earlier_bench_is_never_taken_for_this_runs_result(tmp_path, monkeypatch):
    stale = tmp_path / "cheap" / "run1"
    stale.mkdir(parents=True)
    (stale / "acceptance.json").write_text(json.dumps({"variants": []}))
    monkeypatch.setattr(bench, "arm_argv", lambda *a: [sys.executable, "-c", "pass"])
    run, _ = runner_for(tmp_path, None)
    with pytest.raises(bench.BenchError) as e:
        run(bench.parse_arm(CHEAP), 1)
    assert "no acceptance.json" in str(e.value) and not (stale / "acceptance.json").exists()


def test_an_acceptance_json_the_report_cannot_read_is_an_arm_failure(tmp_path, monkeypatch):
    code = "import json,sys; json.dump({'variants': [{'rows': []}]}, open(sys.argv[1] + '/acceptance.json', 'w'))"
    monkeypatch.setattr(bench, "arm_argv", lambda arm, shared, args, out: [sys.executable, "-c", code, str(out)])
    run, _ = runner_for(tmp_path, None)
    with pytest.raises(bench.BenchError) as e:
        run(bench.parse_arm(CHEAP), 1)
    assert "not a complete report" in str(e.value)


def test_a_run_past_its_time_limit_is_stopped_with_everything_it_started(tmp_path, monkeypatch):
    marker = tmp_path / "child-alive"
    code = ("import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', 'import time,sys; "
            "time.sleep(1.5); open(sys.argv[1], \"w\").write(\"x\")', sys.argv[1]]); time.sleep(30)")
    monkeypatch.setattr(bench, "arm_argv", lambda *a: [sys.executable, "-c", code, str(marker)])
    run, _ = runner_for(tmp_path, None, timeout=1)
    with pytest.raises(bench.BenchError) as e:
        run(bench.parse_arm(CHEAP), 1)
    assert "was stopped" in str(e.value)
    import time
    time.sleep(2.5)
    assert not marker.exists()                         # the grandchild died with the group instead of finishing its work


def test_a_model_credential_is_taken_out_of_the_acceptance_runs_own_environment():
    spec = importlib.util.spec_from_file_location("run_acceptance_for_bench", TESTBED / "run_acceptance.py")
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)
    env = {"HEAL_TEXT_API_KEY": "k", "HEAL_API_KEY": "k", "HEAL_VISION_API_KEY": "k", "HEAL_TEXT_HEADERS": "{}",
           "HEAL_TEXT_EXTRA_BODY": "{}", "HEAL_TEXT_MODEL": "m", "HEAL_SERVICE_TOKEN": "t", "PATH": "/bin"}
    gone = acceptance.scrub_model_credentials(env)
    assert env == {"HEAL_TEXT_MODEL": "m", "HEAL_SERVICE_TOKEN": "t", "PATH": "/bin"} and len(gone) == 5


# ---- the command line -----------------------------------------------------------------------------------------------------------
def test_a_bench_with_only_the_ceiling_is_refused_with_a_usage_error(capsys):
    assert bench.main([]) == 2
    assert "at least one --arm" in capsys.readouterr().err


def test_a_missing_key_variable_stops_the_bench_before_any_arm_runs(monkeypatch, capsys):
    monkeypatch.delenv("CHEAP_KEY", raising=False)
    assert bench.main(["--arm", CHEAP]) == 2
    assert "CHEAP_KEY" in capsys.readouterr().err


def test_the_version_and_commit_for_the_header_are_read_from_the_checkout():
    header = bench.header_of(bench.parse_args(["--arm", CHEAP]))
    assert header["version"] and header["commit"] and header["specs"] == ["drift", "orders"] and header["client"] == "python"
    assert header["date"].endswith("Z")
