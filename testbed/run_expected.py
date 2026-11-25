#!/usr/bin/env python3
"""Run both suites against the base site and its variants WITHOUT healing and write expected.json.

Needs serve.py running (default http://127.0.0.1:8765). For each variant and test it records
passed/failed, the first failing line of the spec, the first line of the error and the kind of
failure the variant was designed to cause.

    python3 run_expected.py [--spec signup|orders] [--port 8765]

signup (default): test_signup.py / signup.spec.ts against base, v1..v5      -> expected.json
orders:           test_orders.py / orders.spec.ts against orders-base, v7..v9 -> expected-orders.json
real:             test_real.py / real.spec.ts against real-base, v10..v12     -> expected-real.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
PY_DIR = os.path.join(HERE, "tests", "python")
TS_DIR = os.path.join(HERE, "tests", "ts")
PY = os.path.join(HERE, "..", "..", ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable
ANSI = re.compile(r"\x1b\[[0-9;]*m")

TESTS = {  # key -> (python test name, ts test title)
    "end_to_end": ("test_signup_end_to_end", "signup end to end"),
    "tabs_accordion": ("test_tabs_and_accordion", "tabs and accordion"),
    "plan_stepper": ("test_plan_table_and_stepper", "plan table and stepper"),
}
ORDERS_TESTS = {
    "grid_toolbar": ("test_orders_grid_and_toolbar", "orders grid and toolbar"),
    "side_panel": ("test_side_panel_and_dialog", "side panel and dialog"),
    "payment_shadow": ("test_payment_frame_and_shadow", "payment frame and shadow"),
}

# intended kind of the FIRST failure (same for both languages), plus what else the variant contains
INTENDED = {
    "base": {k: ("none", []) for k in TESTS},
    "v1": {k: ("drift", []) for k in TESTS},
    "v2": {"end_to_end": ("flow-break", ["missing"]), "tabs_accordion": ("none", []), "plan_stepper": ("none", [])},
    "v3": {"end_to_end": ("flow-break", ["missing"]), "tabs_accordion": ("none", []), "plan_stepper": ("none", [])},
    "v4": {k: ("widget-action", []) for k in TESTS},
    "v5": {"end_to_end": ("drift", ["widget-action", "missing", "flow-break"]),
           "tabs_accordion": ("drift", []), "plan_stepper": ("drift", [])},
}
ORDERS_INTENDED = {
    "base": {k: ("none", []) for k in ORDERS_TESTS},
    "v7": {k: ("drift", []) for k in ORDERS_TESTS},
    "v8": {k: ("drift", []) for k in ORDERS_TESTS},
    "v9": {"grid_toolbar": ("drift", ["missing"]), "side_panel": ("drift", []), "payment_shadow": ("drift", [])},
}

SPECS = {  # spec -> python file, ts file, tests, intended kinds, variants as (name, site dir), output file
    "signup": {"py": "test_signup.py", "ts": "signup.spec.ts", "tests": TESTS, "intended": INTENDED,
               "variants": [(v, v) for v in ("base", "v1", "v2", "v3", "v4", "v5")], "out": "expected.json"},
    "orders": {"py": "test_orders.py", "ts": "orders.spec.ts", "tests": ORDERS_TESTS, "intended": ORDERS_INTENDED,
               "variants": [("base", "orders-base"), ("v7", "v7"), ("v8", "v8"), ("v9", "v9")], "out": "expected-orders.json"},
}


def _real_spec():
    """The real-locator spec: one test per page, tests and intended kinds read from golden-real.json (build_real.py)."""
    path = os.path.join(HERE, "golden-real.json")
    g = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {"tests": {}, "variants": {}}
    tests = {p: (v["python"], v["ts"]) for p, v in g["tests"].items()}
    intended = {"base": {p: ("none", []) for p in tests}}
    for var, heals in g["variants"].items():
        intended[var] = {}
        for p in tests:
            hs = [h for h in heals if h["page"] == p]
            intended[var][p] = ("drift", ["missing"] if any(h["new"] is None for h in hs) else []) if hs else ("none", [])
    return {"py": "test_real.py", "ts": "real.spec.ts", "tests": tests, "intended": intended,
            "variants": [("base", "real-base")] + [(v, v) for v in g["variants"]], "out": "expected-real.json"}


def stmt(path, line):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().split("\n")[line - 1].strip()
    except Exception:
        return None


def run_python(base, pyfile="test_signup.py", cwd=PY_DIR):
    xml = os.path.join(HERE, ".runs", "py.xml")
    os.makedirs(os.path.dirname(xml), exist_ok=True)
    env = dict(os.environ, QA_BASE_URL=base)
    subprocess.run([PY, "-m", "pytest", pyfile, "-q", "--tb=short", f"--junitxml={xml}"],
                   cwd=cwd, env=env, capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
    out = {}
    for tc in ET.parse(xml).getroot().iter("testcase"):
        name = re.sub(r"\[.*\]$", "", tc.get("name"))
        fail = tc.find("failure")
        if fail is None:
            fail = tc.find("error")
        if fail is None:
            out[name] = {"result": "passed"}
            continue
        text = fail.text or ""
        m = re.search(re.escape(pyfile) + r":(\d+): in ", text)
        line = int(m.group(1)) if m else None
        msg = (fail.get("message") or text).strip().split("\n")[0]
        out[name] = {"result": "failed", "line": line, "statement": stmt(os.path.join(cwd, pyfile), line) if line else None,
                     "error": msg}
    return out


def run_ts(base, tsfile="signup.spec.ts", cwd=TS_DIR):
    env = dict(os.environ, QA_BASE_URL=base, FORCE_COLOR="0", NO_COLOR="1")
    r = subprocess.run(["npx", "playwright", "test", tsfile, "--reporter=json"], cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace")
    data = json.loads(r.stdout)
    out = {}

    def walk(suite):
        for spec in suite.get("specs", []):
            res = spec["tests"][0]["results"][-1]
            if res["status"] == "passed":
                out[spec["title"]] = {"result": "passed"}
                continue
            err = (res.get("errors") or [res.get("error") or {}])[0]
            loc = err.get("location") or {}
            line = loc.get("line")
            msg = ANSI.sub("", err.get("message", "")).strip().split("\n")[0]
            out[spec["title"]] = {"result": "failed", "line": line,
                                  "statement": stmt(os.path.join(cwd, tsfile), line) if line else None,
                                  "error": msg}
        for s in suite.get("suites", []):
            walk(s)

    for s in data["suites"]:
        walk(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--spec", choices=sorted(SPECS) + ["real"], default="signup")
    args = ap.parse_args()
    spec = _real_spec() if args.spec == "real" else SPECS[args.spec]
    tests = spec["tests"]
    result = {"server": f"http://127.0.0.1:{args.port}/", "tests": {k: {"python": v[0], "ts": v[1]} for k, v in tests.items()},
              "variants": {}}
    for var, site in spec["variants"]:
        base = f"http://127.0.0.1:{args.port}/{site}/"
        py, ts = run_python(base, spec["py"]), run_ts(base, spec["ts"])
        entry = {}
        for key, (pyname, tsname) in tests.items():
            kind, also = spec["intended"][var][key]
            entry[key] = {"kind": kind, "also_contains": also, "python": py[pyname], "ts": ts[tsname]}
        result["variants"][var] = entry
        print(var, {k: (e["python"]["result"], e["ts"]["result"]) for k, e in entry.items()}, flush=True)
    with open(os.path.join(HERE, spec["out"]), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
