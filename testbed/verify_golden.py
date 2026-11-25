#!/usr/bin/env python3
"""Prove the heals listed in a spec's variants' CHANGES.md: apply them to its suites and run them, no model.

golden-orders.json (written by build_variants.py) and golden-real.json (build_real.py) hold, per variant, every broken locator
expression and its correct replacement (null = the element is gone: the line is deleted). For each variant and language this script

  1. copies the suite to a temp dir and applies ALL heals: every test must pass on that variant (the heals are right
     and nothing else is broken);
  2. with --each, applies all heals but one: the suite must fail, and only at a line that holds the left-out expression, or at
     the assertion right after it (a locator that resolves to the wrong element fails there); so every listed line really breaks,
     and nothing else does.

It starts its own serve.py on a free port. Heals are written in the python syntax; the ts form is derived from it.

    python3 verify_golden.py [--spec orders|real] [--each] [--variants v7 v8 v9] [--lang python ts]
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile

import run_expected as rx

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = rx.SPECS["orders"]


def to_ts(expr):
    """The python locator expression as it is written in tests/ts/orders.spec.ts (same calls, camelCased, single quotes)."""
    s = re.sub(r"\bget_by_(\w+)", lambda m: "getBy" + "".join(p.capitalize() for p in m.group(1).split("_")), expr)
    s = s.replace("frame_locator", "frameLocator")
    s = re.sub(r"(getByRole\([^,()]+), name=([^)]+)\)", r"\1, { name: \2 })", s)
    s = re.sub(r"\.filter\(has_text=([^)]+)\)", r".filter({ hasText: \1 })", s)
    s = re.sub(r"\.(first|last)\b(?!\()", r".\1()", s)
    return re.sub(r'"((?:[^"\\]|\\.)*)"', lambda m: m.group(0) if "'" in m.group(1) else "'" + m.group(1) + "'", s)  # "x" -> 'x' unless x holds a quote


def forms(h, lang):
    """(old, new) of a heal in one language: orders heals are python strings (ts derived), real heals carry both."""
    key = "python" if lang == "python" else "ts"
    if isinstance(h["old"], dict):
        return h["old"][key], (h["new"][key] if h["new"] else None)
    return (h["old"], h["new"]) if lang == "python" else (to_ts(h["old"]), to_ts(h["new"]) if h["new"] else None)


def apply(src, heals, lang):
    out = src
    for h in heals:
        old, new = forms(h, lang)
        n = out.count(old)
        assert n, f"{lang}: {old!r} not found in the test"
        if new is None:                                        # delete the step, keep the line numbers
            out = "\n".join("" if old in l else l for l in out.split("\n"))
        else:
            out = out.replace(old, new)
    return out


def suite(lang, src, tmp):
    """A temp copy of the suite with `src` as the test file; returns the runner for it."""
    if lang == "python":
        shutil.copy2(os.path.join(rx.PY_DIR, "pytest.ini"), tmp)
        name = SPEC["py"]
    else:
        for f in ("package.json", "playwright.config.ts"):
            shutil.copy2(os.path.join(rx.TS_DIR, f), tmp)
        os.symlink(os.path.join(rx.TS_DIR, "node_modules"), os.path.join(tmp, "node_modules"))
        name = SPEC["ts"]
    with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
        f.write(src)
    return name


def run(lang, base, src):
    with tempfile.TemporaryDirectory() as tmp:
        name = suite(lang, src, tmp)
        return rx.run_python(base, name, tmp) if lang == "python" else rx.run_ts(base, name, tmp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--each", action="store_true", help="also leave each heal out in turn and check where it fails")
    ap.add_argument("--spec", choices=["orders", "real"], default="orders")
    ap.add_argument("--variants", nargs="*", default=None)
    ap.add_argument("--lang", nargs="*", default=["python", "ts"], choices=["python", "ts"])
    a = ap.parse_args()
    global SPEC
    if a.spec == "real":
        SPEC = rx._real_spec()
        golden = json.load(open(os.path.join(HERE, "golden-real.json"), encoding="utf-8"))["variants"]
    else:
        golden = json.load(open(os.path.join(HERE, "golden-orders.json"), encoding="utf-8"))
    a.variants = a.variants or list(golden)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, os.path.join(HERE, "serve.py"), "--port", str(port)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ, QA_SERVE_QUIET="1"))
    bad = 0
    try:
        import time
        time.sleep(0.8)
        for lang in a.lang:
            path = os.path.join(rx.PY_DIR if lang == "python" else rx.TS_DIR, SPEC["py"] if lang == "python" else SPEC["ts"])
            src = open(path, encoding="utf-8").read()
            names = {k: (v[0] if lang == "python" else v[1]) for k, v in SPEC["tests"].items()}
            for var in a.variants:
                base = f"http://127.0.0.1:{port}/{var}/"
                heals = golden[var]
                res = run(lang, base, apply(src, heals, lang))
                fails = {n: r for n, r in res.items() if r["result"] != "passed"}
                ok = len(res) == len(names) and not fails
                bad += not ok
                print(f"{lang:6} {var} all {len(heals)} heals applied: {'PASS' if ok else 'FAIL ' + json.dumps(fails)}", flush=True)
                if not a.each:
                    continue
                for i, h in enumerate(heals):
                    res = run(lang, base, apply(src, heals[:i] + heals[i + 1:], lang))
                    old = forms(h, lang)[0]
                    lines = src.split("\n")
                    fail_lines = [(n, r.get("line")) for n, r in res.items() if r["result"] != "passed"]
                    where = [(n, l) for n, l in fail_lines if l and (old in lines[l - 1] or (l > 1 and old in lines[l - 2]))]
                    ok = bool(fail_lines) and len(where) == len(fail_lines)
                    bad += not ok
                    print(f"{lang:6} {var}   without {old[:70]!r}: fails at {[l for _, l in fail_lines]} {'ok' if ok else 'UNEXPECTED'}", flush=True)
    finally:
        server.terminate()
        server.wait(timeout=10)
    print("verify_golden:", "OK" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
