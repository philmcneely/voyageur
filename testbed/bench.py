#!/usr/bin/env python3
"""
===============================================================================
Model Bench (the testbed run once per model, the results side by side)
===============================================================================
Compares backend models on the same broken suites. Each arm is a name and the settings of one model (base URL, model id, provider,
the NAME of the environment variable that holds its API key, an optional extra request body and a price). The bench runs
testbed/run_acceptance.py once per arm and repeat, with the same specs, client, candidate cap, call limit, token limit and
timeouts for every arm, then writes one comparison (bench.md and bench.json, built by bench_report.py): healed, needs_human by
reason, wrong heals kept, wrong element caught, steps run against the golden steps, model calls, tokens, cost, latency, the steps
where the arms picked differently, and a fit line per arm. The scripted `fake` arm is always the first column: it is the ceiling,
what the pipeline gives when the model's pick is right.

How an arm runs: one child process of run_acceptance.py `--model env` with that arm's HEAL_TEXT_* variables. The child starts from
the caller's environment with every HEAL_* variable removed (but HEAL_MODEL_PRICES, which every arm shares, and HEAL_TS_CLIENT_DIR),
so a variable set in the caller's shell cannot give one arm an advantage. The API key is read from the variable the arm names and
goes into the child's environment only: never its command line, the report or the log. A scripted arm (`kind=fake`) is
deterministic and runs once whatever `--repeat` says; it may set the pipeline's own knobs (`candidate_cap`, `max_calls`), which the
report then lists as differing. It exists to prove the runner and to show how much a setting alone moves the numbers.

The package has no `voyageur bench` command: the bench needs the testbed (sites, suites, golden files, a Playwright browser), which is
not in the wheel, and `src/` may not run programs (tests/unit/test_source_hygiene.py), so the CLI would be a wrapper that cannot work
from an installed package. It lives in the checkout, next to the golden files it measures against.

Features:
    ✓ Arms from repeated `--arm` flags (`name=cheap,base_url=...,model=...,api_key_env=CHEAP_KEY`, or a JSON object) and/or an
      `--arms-file` (JSON or TOML); the ceiling arm is added by the bench
    ✓ One set of shared settings (max tokens, candidate cap, max calls, model timeout, action timeout, thinking, fingerprints) given
      once and printed in the report, so fairness can be checked
    ✓ `--repeat N` runs each endpoint arm N times (min, max and mean in the report); arms run one after the other, or together
      with `--parallel-arms` (the repeats of one arm never overlap)
    ✓ A missing key variable or a malformed arm stops the bench before anything runs (exit status 2)
    ✓ An arm whose run produced nothing is reported as failed and the other arms still run
    ✓ Exit status 1 when an arm failed to run or the scripted ceiling kept a wrong heal (a gate failed, whatever the models did);
      a real model's misses and its wrong heals are findings in the report, not a failure of the bench

Environment Variables:
    <the variable each arm names with api_key_env>   that arm's API key (read here, handed to that arm's run only)
    HEAL_MODEL_PRICES                                JSON {"model": {"input": n, "output": n}} per million tokens, shared by every arm
    HEAL_TS_CLIENT_DIR                               --client ts: the TypeScript client's repository (or --ts-client-dir)
    every other HEAL_* variable is dropped from the arms' environment

Author: PMAC
===============================================================================
"""
import argparse
import concurrent.futures
import json
import os
import re
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

import bench_report  # noqa: E402  (the report builder, tested on its own)

SPECS = ("drift", "orders", "real")
DEFAULT_SPECS = ["drift", "orders"]
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")       # CHEAP_KEY: capitals with an underscore, which a pasted key rarely has
KEEP_ENV = ("HEAL_MODEL_PRICES", "HEAL_TS_CLIENT_DIR")      # the only HEAL_* variables an arm's run inherits
ARM_KEYS = {"name", "kind", "model", "provider", "base_url", "api_key_env", "extra_body", "price_input", "price_output",
            "candidate_cap", "max_calls"}
FAKE_KEYS = {"name", "kind", "candidate_cap", "max_calls"}


class BenchError(ValueError):
    """A setting or a run the bench cannot use. The message names the field, never a value that could be a secret."""


@dataclass(frozen=True)
class Arm:
    name: str
    kind: str = "endpoint"                       # endpoint: a model behind HEAL_TEXT_*;  fake: the scripted model
    model: Optional[str] = None
    provider: str = "openai"
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None            # the NAME of the variable that holds the key
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    price: Optional[Tuple[float, float]] = None  # per million tokens, input and output
    overrides: Mapping[str, int] = field(default_factory=dict)       # a scripted arm's own candidate_cap / max_calls


# ---- arms ---------------------------------------------------------------------------------------------------------------------
def _int(key: str, value: Any, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum - 1
    if number < minimum:
        raise BenchError(f"{key} must be a whole number of at least {minimum}")
    return number


def _price(key: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = -1.0
    if not number >= 0 or number == float("inf"):
        raise BenchError(f"{key} must be a number of at least 0 (a price per million tokens)")
    return number


def arm_from_dict(data: Mapping[str, Any]) -> Arm:
    """One arm from its fields; BenchError names the field that cannot be used."""
    unknown = sorted(set(data) - ARM_KEYS)
    if unknown:
        shown = [k if re.fullmatch(r"[a-z_]{1,24}", str(k)) else "a field with an unusable name" for k in unknown]
        raise BenchError(f"an arm may not set {', '.join(shown)} (it may set {', '.join(sorted(ARM_KEYS))})")
    key_env = data.get("api_key_env")
    secret = (os.environ.get(str(key_env)) or "").strip() if key_env and ENV_NAME.fullmatch(str(key_env)) else ""
    if len(secret) >= 8 and any(secret in str(data.get(k) or "") for k in ("name", "model", "base_url")):
        raise BenchError("an arm's name, model or base_url holds the value of an API key variable "
                         "(a key goes in the environment; api_key_env names the variable)")
    name = str(data.get("name") or "")
    if not NAME.fullmatch(name):
        raise BenchError("an arm needs a name of letters, digits, dot, dash and underscore (at most 40)")
    if name.lower() == bench_report.CEILING:
        raise BenchError(f"the name {bench_report.CEILING} is the scripted ceiling's: the bench adds that arm itself")
    kind = str(data.get("kind") or "endpoint")
    if kind not in ("endpoint", "fake"):
        raise BenchError(f"arm {name}: kind must be endpoint or fake")
    if kind == "fake":
        extra = sorted(set(data) - FAKE_KEYS)
        if extra:
            raise BenchError(f"arm {name}: a scripted arm names no endpoint (it may set {', '.join(sorted(FAKE_KEYS - {'name'}))})")
        return Arm(name=name, kind="fake", overrides={k: _int(k, data[k]) for k in ("candidate_cap", "max_calls") if k in data})
    for knob in ("candidate_cap", "max_calls"):
        if knob in data:
            raise BenchError(f"arm {name}: {knob} is shared by every arm (set it once, with --{knob.replace('_', '-')})")
    for key in ("base_url", "model"):
        if not data.get(key):
            raise BenchError(f"arm {name}: {key} is required")
    from voyageur.llm import LLMConfigError, _base_url, _extra_body          # the service's own rules for both
    try:
        _base_url(f"arm {name}: base_url", str(data["base_url"]))
    except LLMConfigError as e:
        raise BenchError(str(e)) from None
    provider = str(data.get("provider") or "openai")
    if provider not in ("openai", "anthropic"):
        raise BenchError(f"arm {name}: provider must be openai or anthropic")
    key_env = data.get("api_key_env")
    if key_env is not None and not ENV_NAME.fullmatch(str(key_env)):
        raise BenchError(f"arm {name}: api_key_env must be the NAME of an environment variable, in capitals with an underscore "
                         "like CHEAP_KEY (not the key itself)")
    body = data.get("extra_body") or {}
    if not isinstance(body, Mapping):
        raise BenchError(f"arm {name}: extra_body must be a JSON object")
    try:
        body = _extra_body(f"arm {name}: extra_body", json.dumps(body))
    except TypeError:
        raise BenchError(f"arm {name}: extra_body must hold only JSON values") from None
    except LLMConfigError as e:
        raise BenchError(str(e)) from None
    prices = [k for k in ("price_input", "price_output") if data.get(k) is not None]
    if prices and len(prices) != 2:
        raise BenchError(f"arm {name}: a price needs both price_input and price_output")
    price = (_price("price_input", data["price_input"]), _price("price_output", data["price_output"])) if prices else None
    return Arm(name=name, model=str(data["model"]), provider=provider, base_url=str(data["base_url"]),
               api_key_env=str(key_env) if key_env else None, extra_body=dict(body), price=price)


def parse_arm(text: str) -> Arm:
    """An --arm flag: `key=value,key=value` (no commas inside a value), or a JSON object when a value needs them (extra_body)."""
    text = text.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            raise BenchError("an --arm that starts with { must be a JSON object") from None
        if not isinstance(data, dict):
            raise BenchError("an --arm that starts with { must be a JSON object")
        return arm_from_dict(data)
    data = {}
    for part in text.split(","):
        key, sep, value = part.partition("=")
        if not sep or not key.strip():
            raise BenchError("an --arm is key=value pairs separated by commas, like name=cheap,base_url=...,model=...")
        data[key.strip()] = value.strip()
    return arm_from_dict(data)


def load_arms_file(path: Path) -> List[Arm]:
    """Arms from a JSON file ({"arms": [...]} or a list) or a TOML file ([[arms]] tables)."""
    try:
        raw = Path(path).read_bytes()
        data = tomllib.loads(raw.decode("utf-8")) if str(path).endswith(".toml") else json.loads(raw)
    except (OSError, ValueError) as e:
        raise BenchError(f"the arms file cannot be read: {type(e).__name__}") from None
    items = data.get("arms") if isinstance(data, dict) else data
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise BenchError("the arms file must hold a list of arms (JSON: {\"arms\": [...]}, TOML: [[arms]])")
    return [arm_from_dict(i) for i in items]


def arms_from(user_arms: Sequence[Arm]) -> List[Arm]:
    """The scripted ceiling first, then the arms as given. At least one arm besides the ceiling; names are unique."""
    names = [a.name.lower() for a in user_arms]          # a name is a directory: Cheap and cheap are one on a case-blind disk
    if len(set(names)) != len(names):
        raise BenchError("arm names must be unique (ignoring case)")
    if not user_arms:
        raise BenchError("give at least one --arm (or an --arms-file) besides the scripted ceiling")
    return [Arm(name=bench_report.CEILING, kind="fake")] + list(user_arms)


# ---- the settings every arm shares and the run of one arm ---------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Compare models on the testbed: one acceptance run per arm, one report.")
    ap.add_argument("--arm", action="append", default=[], metavar="SPEC",
                    help="a model arm: name=cheap,base_url=http://localhost:8000/v1,model=small,provider=openai,api_key_env=CHEAP_KEY "
                         "(optional price_input= and price_output= per million tokens), or a JSON object; repeat the flag for more arms")
    ap.add_argument("--arms-file", default=None, help="arms in a JSON ({\"arms\": [...]}) or TOML ([[arms]]) file")
    ap.add_argument("--spec", nargs="*", default=DEFAULT_SPECS, choices=SPECS, help="specs to run (default drift orders)")
    ap.add_argument("--variants", nargs="*", default=None, help="only these variants")
    ap.add_argument("--client", choices=["python", "ts"], default="python")
    ap.add_argument("--ts-client-dir", default=os.environ.get("HEAL_TS_CLIENT_DIR"))
    ap.add_argument("--repeat", type=int, default=1, help="runs per endpoint arm (default 1; a scripted arm always runs once)")
    ap.add_argument("--parallel-arms", action="store_true", help="run the arms at the same time (default: one after the other)")
    ap.add_argument("--max-tokens", type=int, default=None, help="max output tokens of a model call, every arm (default: the service's 64)")
    ap.add_argument("--candidate-cap", type=int, default=None, help="lines in the pick list, every arm (default: the service's 30)")
    ap.add_argument("--max-calls", type=int, default=None, help="model calls per heal, every arm (default: the service's 2)")
    ap.add_argument("--timeout-s", type=int, default=None, help="seconds one model request may take, every arm (default: the service's 120)")
    ap.add_argument("--action-timeout-ms", type=int, default=1500, help="action timeout of the suites (default 1500)")
    ap.add_argument("--thinking", choices=["on", "off"], default="off", help="HEAL_TEXT_THINKING for every arm (default off)")
    ap.add_argument("--fingerprints", choices=["on", "off"], default="on",
                    help="off: every heal starts at the text pick, so every heal reaches the model (default on)")
    ap.add_argument("--run-timeout-s", type=int, default=7200, help="seconds one acceptance run may take (default 7200)")
    ap.add_argument("--out", default=str(HERE / ".runs" / "bench"), help="directory for bench.md, bench.json and each run's files")
    return ap.parse_args(argv)


def shared_settings(args: argparse.Namespace) -> Dict[str, Any]:
    """The settings every arm runs with, defaults resolved from the service itself, so the report prints real values."""
    from voyageur.config import ServiceSettings
    from voyageur.llm import DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT_S
    base = ServiceSettings()
    return {"max_tokens": args.max_tokens or DEFAULT_MAX_TOKENS, "candidate_cap": args.candidate_cap or base.candidate_cap,
            "max_calls": args.max_calls or base.max_calls, "timeout_s": args.timeout_s or DEFAULT_TIMEOUT_S,
            "action_timeout_ms": args.action_timeout_ms, "thinking": args.thinking, "fingerprints": args.fingerprints,
            "vision": "off"}


def arm_env(arm: Arm, shared: Mapping[str, Any], base: Mapping[str, str]) -> Dict[str, str]:
    """The environment of one arm's run: the caller's, less every HEAL_* variable but the shared ones, plus the arm's model."""
    env = {k: v for k, v in base.items() if not k.startswith("HEAL_") or k in KEEP_ENV}
    prices: Dict[str, Any] = {}
    if env.get("HEAL_MODEL_PRICES"):
        try:
            prices = json.loads(env["HEAL_MODEL_PRICES"])
        except ValueError:
            raise BenchError("HEAL_MODEL_PRICES is not valid JSON") from None
        if not isinstance(prices, dict):
            raise BenchError("HEAL_MODEL_PRICES must be a JSON object")
    env.update({"HEAL_CANDIDATE_CAP": str(shared["candidate_cap"]), "HEAL_MAX_CALLS": str(shared["max_calls"]),
                "HEAL_TEXT_MAX_TOKENS": str(shared["max_tokens"]), "HEAL_TEXT_TIMEOUT_S": str(shared["timeout_s"]),
                "HEAL_TEXT_THINKING": str(shared["thinking"])})
    if arm.kind == "endpoint":
        env.update({"HEAL_TEXT_PROVIDER": str(arm.provider), "HEAL_TEXT_BASE_URL": str(arm.base_url), "HEAL_TEXT_MODEL": str(arm.model)})
        if arm.api_key_env:
            key = (base.get(arm.api_key_env) or "").strip()
            if not key:
                raise BenchError(f"arm {arm.name}: the environment variable {arm.api_key_env} is not set (or is blank)")
            env["HEAL_TEXT_API_KEY"] = key
        if arm.extra_body:
            env["HEAL_TEXT_EXTRA_BODY"] = json.dumps(arm.extra_body)
        if arm.price:
            prices[str(arm.model)] = {"input": arm.price[0], "output": arm.price[1]}
    if prices:
        env["HEAL_MODEL_PRICES"] = json.dumps(prices)
    return env


def check_keys_stay_out_of_fields(arms: Sequence[Arm], base: Mapping[str, str]) -> None:
    """The name, model and URL of an arm are printed in the report and become a directory name. A key pasted into one of them
    (instead of its variable's name in api_key_env) is refused before anything runs, without echoing it."""
    secrets = [v for a in arms if a.api_key_env for v in [(base.get(a.api_key_env) or "").strip()] if len(v) >= 8]
    for n, arm in enumerate(arms, start=1):
        for field_name, text in (("name", arm.name), ("model", arm.model or ""), ("base_url", arm.base_url or ""),
                                 ("api_key_env", arm.api_key_env or "")):
            if any(s in text for s in secrets):
                raise BenchError(f"arm number {n}: its {field_name} holds the value of an API key variable "
                                 "(a key goes in the environment; api_key_env names the variable)")


def arm_argv(arm: Arm, shared: Mapping[str, Any], args: argparse.Namespace, out_dir: Path) -> List[str]:
    """The command of one arm's run. Nothing secret is on it: the key is in the environment."""
    argv = [sys.executable, str(HERE / "run_acceptance.py"), "--model", "fake" if arm.kind == "fake" else "env",
            "--spec", *args.spec, "--client", args.client, "--timeout-ms", str(shared["action_timeout_ms"]),
            "--fingerprints", str(shared["fingerprints"]), "--out", str(out_dir)]
    if args.variants:
        argv += ["--variants", *args.variants]
    if args.client == "ts" and args.ts_client_dir:
        argv += ["--ts-client-dir", str(args.ts_client_dir)]
    if arm.kind == "fake":                           # a scripted model has no endpoint: its pipeline knobs go on the command line
        argv += ["--candidate-cap", str(arm.overrides.get("candidate_cap", shared["candidate_cap"])),
                 "--max-calls", str(arm.overrides.get("max_calls", shared["max_calls"]))]
    return argv


def make_runner(args: argparse.Namespace, shared: Mapping[str, Any], out_root: Path, base_env: Mapping[str, str]) -> Callable[[Arm, int], Dict[str, Any]]:
    """The function that runs one arm once: a child process, its log, and its acceptance.json."""
    def run(arm: Arm, n: int) -> Dict[str, Any]:
        out_dir = out_root / arm.name / f"run{n}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "acceptance.json"
        path.unlink(missing_ok=True)                  # a file left by an earlier bench must never pass for this run's result
        started = time.time()
        with open(out_dir / "run.log", "w", encoding="utf-8") as log:
            # its own process group, so a run that passes its time limit is stopped with the browsers and test runners it started
            proc = subprocess.Popen(arm_argv(arm, shared, args, out_dir), env=arm_env(arm, shared, base_env), stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = proc.wait(timeout=args.run_timeout_s)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise BenchError(f"the run of {arm.name} passed {args.run_timeout_s} s and was stopped") from None
        if not path.exists():
            raise BenchError(f"the run of {arm.name} produced no acceptance.json (exit {code}; see {out_dir / 'run.log'})")
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            bench_report.run_summary(data)            # a file the report cannot read is this arm's failure, not the bench's
        except (TypeError, KeyError, AttributeError):
            raise BenchError(f"the acceptance.json of {arm.name} is not a complete report (see {out_dir / 'run.log'})") from None
        data["bench_run"] = {"exit_code": code, "seconds": round(time.time() - started, 1), "dir": str(out_dir)}
        print(f"  {arm.name} run {n}: exit {code}, {data['bench_run']['seconds']} s", flush=True)
        return data
    return run


def run_all(arms: Sequence[Arm], repeat: int, parallel: bool, runner: Callable[[Arm, int], Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run every arm; each endpoint arm `repeat` times, a scripted arm once. The repeats of one arm run one after the other.
    Returns [{"arm", "runs", "error"}] in arm order. An arm whose run fails stops there and keeps the runs it finished."""
    def one(arm: Arm) -> Dict[str, Any]:
        runs: List[Dict[str, Any]] = []
        error: Optional[str] = None
        for n in range(1, (1 if arm.kind == "fake" else repeat) + 1):
            try:
                runs.append(runner(arm, n))
            except (BenchError, OSError, ValueError, TypeError, KeyError) as e:      # ValueError: an acceptance.json that is not JSON
                error = str(e) if isinstance(e, BenchError) else f"the run of {arm.name} failed: {type(e).__name__}"
                break
        return {"arm": arm, "runs": runs, "error": error}
    if not parallel:
        return [one(a) for a in arms]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(arms))) as pool:
        return list(pool.map(one, arms))


# ---- the report ---------------------------------------------------------------------------------------------------------------
def header_of(args: argparse.Namespace) -> Dict[str, Any]:
    from voyageur import __version__
    try:
        top = HERE.parent
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=top, capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=top, capture_output=True, text=True,
                               timeout=10).stdout.strip()
        commit = (commit + (" (uncommitted changes)" if dirty else "")) if commit else "unknown"
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    return {"date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "version": __version__, "commit": commit,
            "specs": list(args.spec), "client": args.client, "repeat": args.repeat}


def report_of(args: argparse.Namespace, shared: Mapping[str, Any], results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    arms = []
    for r in results:
        a = r["arm"]
        arms.append({"name": a.name, "kind": a.kind, "model": a.model or ("scripted" if a.kind == "fake" else None),
                     "provider": a.provider if a.kind == "endpoint" else None, "base_url": a.base_url, "api_key_env": a.api_key_env,
                     "settings": dict(a.overrides), "runs": r["runs"], "error": r["error"],
                     "extra_body_keys": sorted(a.extra_body)})        # the names only: a value may be a credential
    return bench_report.build(header_of(args), shared, arms)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.repeat < 1:
            raise BenchError("--repeat must be at least 1")
        user = [parse_arm(text) for text in args.arm] + (load_arms_file(Path(args.arms_file)) if args.arms_file else [])
        arms = arms_from(user)
        if args.client == "ts" and not args.ts_client_dir:
            raise BenchError("--client ts needs --ts-client-dir (or $HEAL_TS_CLIENT_DIR)")
        shared = shared_settings(args)
        check_keys_stay_out_of_fields(arms, os.environ)
        for arm in arms:                         # every key variable and price is checked before the first run starts
            arm_env(arm, shared, os.environ)
    except BenchError as e:
        print(f"bench: {e}", file=sys.stderr)
        return 2
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    print(f"bench: {len(arms)} arms ({', '.join(a.name for a in arms)}), specs {' '.join(args.spec)}, repeat {args.repeat}"
          f"{', arms in parallel' if args.parallel_arms else ''}", flush=True)
    for arm in arms:                             # where each key is about to be sent, before it is
        if arm.api_key_env:
            print(f"bench: arm {arm.name} sends ${arm.api_key_env} to {urlsplit(str(arm.base_url)).netloc}", flush=True)
    started = time.time()
    results = run_all(arms, args.repeat, args.parallel_arms, make_runner(args, shared, out, os.environ))
    report = report_of(args, shared, results)
    (out / "bench.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    (out / "bench.md").write_text(bench_report.markdown(report), encoding="utf-8")
    print(f"\nbench: {round(time.time() - started)} s")
    for fit in report["fit"]:
        print(" ", fit["line"])
    print(f"wrote {out / 'bench.md'} and {out / 'bench.json'}")
    ceiling = next(r for r in results if r["arm"].name == bench_report.CEILING)
    broken = [r["arm"].name for r in results if r["error"]]
    kept = sum(bench_report.run_summary(run)["wrong_kept"] for run in ceiling["runs"])
    if broken:
        print(f"bench: no result from {', '.join(broken)}", file=sys.stderr)
    if kept:
        print(f"bench: the scripted ceiling kept {kept} wrong heal(s): a gate failed, whatever the models did; see its run.log",
              file=sys.stderr)
    for run in ceiling["runs"]:
        if run["bench_run"]["exit_code"] != 0:
            print("bench: note: the scripted ceiling missed some of the golden file's expectations under these settings "
                  f"(the golden file assumes the defaults); see {run['bench_run']['dir']}/acceptance.md", file=sys.stderr)
    return 1 if broken or kept else 0


if __name__ == "__main__":
    sys.exit(main())
