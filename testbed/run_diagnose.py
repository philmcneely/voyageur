#!/usr/bin/env python3
"""
===============================================================================
Testbed Diagnose Run (real failures that are not locator drift, through POST /v1/diagnose)
===============================================================================
Serves the testbed's static sites and, for each case in golden-diagnose.json, runs one test of tests/python/test_diagnose.py against
a variant that breaks the page in a way no locator can mend (a reworded message, a dead button, a late render, a refused input, a new
confirmation step, a duplicate button after a wrong heal). The earlier heals a case lists are applied to the test first, exactly as a
heal client would have left it. The failing run dumps the page as it was at the failure (the probe's listing and the aria snapshot);
this script builds the POST /v1/diagnose request from it and judges the answer against the case's key. The repository's own test files
are never edited and no endpoint is called with `--model fake`.

The model is scripted (`--model fake`, offline): the runner answers each diagnose prompt from the key (`oracle`) or as an adversary
would. The adversaries are the point of the run: a model that says earlier_heal_wrong_element for every failure, naming the real
heal id (`real-id`), an id nobody sent (`made-up-id`) or an id the request did not list (`empty-priors`: the request carries no
prior_heals), and one that proposes an edit whatever the cause (`patch-always`). The service must not keep any of those claims
that the evidence does not carry:
    wrong cause kept           the service returns earlier_heal_wrong_element and the key says otherwise or names another heal
    edit on a non-edit cause   the service returns an edit for a cause that may not carry one (app_bug, test_data_or_env, unknown ...)
Both must be 0 under every script, and under `oracle` every answer must equal the key. `--model env` (the service's own HEAL_TEXT_*
model) answers for real; its per-cause accuracy and confusion matrix are printed as information only and gate nothing.

Features:
    ✓ Fully offline with --model fake; the same request path drives a real model
    ✓ Cases whose failure the service must not diagnose (a locator that no longer matches is drift) are in the key too: not_applicable
      and no model call
    ✓ Reports per script: answers equal to the key, wrong cause kept, edits on a non-edit cause, answers the service downgraded,
      model calls and tokens; and per case what each script got
    ✓ Exit status 1 when a case cannot be run, `oracle` misses its key, or either gated count is not 0 under a scripted model
    ✓ Nothing is run by the existing specs: run_acceptance.py is imported for its service, workdir and site server, not changed

Environment Variables:
    HEAL_TEXT_BASE_URL, HEAL_TEXT_MODEL, HEAL_TEXT_API_KEY, HEAL_TEXT_PROVIDER   (--model env) the model the service uses

Author: PMAC
===============================================================================
"""
import argparse
import dataclasses
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

import run_acceptance as ra  # noqa: E402  (its service, workdir and site server)

ra.SPECS.setdefault("diagnose", {"file": "test_diagnose.py", "base": "drift-base", "golden": "golden-diagnose.json"})
GOLDEN = HERE / "golden-diagnose.json"
SCRIPTS = ("oracle", "real-id", "made-up-id", "empty-priors", "patch-always")
MADE_UP = "h_" + "0" * 24
PATCH_CAUSES = ("stale_expectation", "flow_changed", "timing")
CAUSES = ("stale_expectation", "app_bug", "timing", "test_data_or_env", "earlier_heal_wrong_element", "flow_changed", "unknown")


class Scripted:
    """The scripted model: it returns the reply the runner set (None = no answer) and a fixed usage estimate (four characters a
    token), so the figures are reproducible."""

    def __init__(self) -> None:
        self.reply: Optional[str] = None
        self.calls = 0

    def call(self, endpoint: Any, prompt: str, images: Any = (), heal_id: str = "") -> Any:
        from voyageur.llm import Usage
        from voyageur.transport import CallResult
        self.calls += 1
        return CallResult(self.reply, "" if self.reply else "no answer",
                          Usage(max(1, len(prompt) // 4), max(1, len(self.reply or "") // 4)), 1, True)


def line_of(text: str, needle: str) -> int:
    return text.count("\n", 0, text.index(needle)) + 1


def prepare(case: Dict[str, Any], original: str, test_file: str) -> Dict[str, Any]:
    """The test source as the earlier heals left it and the prior_heals a client would send. `applied` false leaves the source
    alone (a heal already reverted); `file` another than the test's is a heal in a file that is not sent."""
    text, heals = original, []
    for i, p in enumerate(case["prior_heals"]):
        heal_id = "h_" + hashlib.sha256(f"{case['id']}/{i}".encode()).hexdigest()[:24]
        if p.get("file"):
            heals.append({"heal_id": heal_id, "file": p["file"], "line": p["line"], "from": p["from"], "to": p["to"]})
            continue
        line = line_of(original, p["anchor"])
        if p["applied"]:
            lines = text.split("\n")
            lines[line - 1] = lines[line - 1].replace(p["from"], p["to"], 1)
            text = "\n".join(lines)
        heals.append({"heal_id": heal_id, "file": test_file, "line": line, "from": p["from"], "to": p["to"]})
    return {"source": text, "prior_heals": heals}


def request_body(case: Dict[str, Any], wd: Any, dump: Dict[str, Any], res: Dict[str, Any], prepared: Dict[str, Any]) -> Dict[str, Any]:
    source = prepared["source"]
    record = dump["probe"]["page"]
    return {
        "client": {"framework": "voyageur-testbed", "language": "python", "version": "1"},
        "test": {"id": f"{wd.test_file}::{case['test']}", "title": case["test"]},
        "failure": {"error": res["error"], "stack": [{"file": f, "line": n} for f, n in res["frames"]]},
        "source": {"roots": [], "files": [{"path": wd.test_file, "sha256": hashlib.sha256(source.encode()).hexdigest(),
                                           "content": source}]},
        "page": {"url": dump["url"],
                 "language": {"html_lang": record.get("lang") or None, "browser_locale": record.get("browser_lang") or None},
                 "inventory": dump["probe"]["items"], "inventory_more": dump["probe"].get("more", 0),
                 "inventory_copy_more": dump["probe"].get("copy_more", 0),
                 "inventory_copy_withheld": dump["probe"].get("copy_withheld", 0),
                 "inventory_copy_withheld_data": dump["probe"].get("copy_withheld_data", 0), "aria_snapshot": dump.get("aria")},
        "options": {}, "run": {"id": f"diagnose-{case['id']}", "repo": "testbed"}, "prior_heals": prepared["prior_heals"]}


def capture(case: Dict[str, Any], root: str, probe_path: Path, timeout_ms: int) -> Dict[str, Any]:
    """Run the case's test on its variant and return the request a client would send, or {"error": why}."""
    wd = ra.Workdir("diagnose", f"{root}/{case['variant']}/", probe_path, timeout_ms)
    try:
        prepared = prepare(case, wd.text, wd.test_file)
        wd.write(prepared["source"])
        res = wd.run(case["test"])[0].get(case["test"]) or {"result": "missing"}
        if res["result"] != "failed":
            return {"error": f"the test did not fail on {case['variant']} ({res['result']})"}
        dump = wd.failure(case["test"])
        if dump is None or "probe" not in dump or not res["frames"]:
            return {"error": "the failing run left no evidence (no page dump or no stack frame in the test file)"}
        return {"body": request_body(case, wd, dump, res, prepared), "source": prepared["source"], "line": res["line"],
                "file": wd.test_file}
    finally:
        shutil.rmtree(wd.dir, ignore_errors=True)


# ---- the scripted models ---------------------------------------------------------------------------------------------
def reply_json(cause: str, heal_id: Optional[str] = None, patch: Optional[Dict[str, Any]] = None, line: int = 0) -> str:
    return json.dumps({"cause": cause, "evidence": [f"line {line}: scripted"], "confidence": "high", "heal_id": heal_id,
                       "patch": patch, "why": "scripted"})


def script_reply(script: str, case: Dict[str, Any], cap: Dict[str, Any]) -> Optional[str]:
    """What the scripted model says for this case (None when the service must not ask a model at all)."""
    exp = case["expect"]
    if "cause" not in exp:
        return reply_json("app_bug")                          # never asked: a located failure is not diagnosable
    priors = cap["prepared_priors"]
    first = priors[0]["heal_id"] if priors else MADE_UP
    if script == "real-id" or script == "empty-priors":
        return reply_json("earlier_heal_wrong_element", first, line=cap["line"])
    if script == "made-up-id":
        return reply_json("earlier_heal_wrong_element", MADE_UP, line=cap["line"])
    heal_id = priors[exp["revert_idx"]]["heal_id"] if exp["revert_idx"] is not None else None
    patch = None
    if script == "patch-always":                              # an edit of the failing statement, whatever the cause
        failing = cap["source"].split("\n")[cap["line"] - 1].strip()
        patch = {"file": cap["file"], "line": cap["line"], "from": failing, "to": "pass", "why": "scripted"}
    elif exp["patch"]:
        patch = {"file": cap["file"], "line": line_of(cap["source"], exp["patch"]["from"]), "from": exp["patch"]["from"],
                 "to": exp["patch"]["to"], "why": "scripted from the key"}
    return reply_json(exp["cause"], heal_id, patch, cap["line"])


def judge(case: Dict[str, Any], resp: Dict[str, Any], priors: List[Dict[str, Any]]) -> Dict[str, Any]:
    """How an answer stands against the key: equal to it, a wrong cause kept, an edit on a cause that may not carry one."""
    exp, d = case["expect"], resp.get("diagnosis")
    want_id = priors[exp["revert_idx"]]["heal_id"] if exp.get("revert_idx") is not None else None
    kept_wrong = bool(d and d["cause"] == "earlier_heal_wrong_element"
                      and (exp.get("cause") != "earlier_heal_wrong_element" or d["revert_heal_id"] != want_id))
    bad_edit = bool(d and d["suggested_patch"] and d["cause"] not in PATCH_CAUSES)
    if "status" in exp:
        equal = resp["status"] == exp["status"] and resp["usage"]["calls"] == 0
    else:
        patch = d["suggested_patch"] if d else None
        want = exp["patch"]
        equal = bool(d and resp["status"] == "diagnosed" and d["cause"] == exp["cause"] and d["revert_heal_id"] == want_id
                     and (patch is None) == (want is None) and (want is None or (patch["from"], patch["to"]) == (want["from"], want["to"])))
    return {"equal": equal, "wrong_cause_kept": kept_wrong, "bad_edit_kept": bad_edit}


def run_script(svc: Any, model: Optional[Scripted], script: str, cases: List[Dict[str, Any]], captured: Dict[str, Dict[str, Any]],
               real: bool) -> Dict[str, Any]:
    rows = []
    for case in cases:
        cap = captured[case["id"]]
        if "error" in cap:
            rows.append({"id": case["id"], "error": cap["error"]})
            continue
        body = json.loads(json.dumps(cap["body"]))
        priors = list(body["prior_heals"])
        cap["prepared_priors"] = priors
        if script == "empty-priors":
            body["prior_heals"] = []
        if model is not None:
            model.reply = script_reply(script, case, cap)
        resp = svc.post("/v1/diagnose", body)
        j = judge(case, resp, priors)
        d = resp.get("diagnosis") or {}
        rows.append({"id": case["id"], "status": resp["status"], "cause": d.get("cause"), "confidence": d.get("confidence"),
                     "revert": d.get("revert_heal_id") is not None, "patch": bool(d.get("suggested_patch")),
                     "downgraded": d.get("downgraded"), "calls": resp["usage"]["calls"], "input_tokens": resp["usage"]["input_tokens"],
                     "output_tokens": resp["usage"]["output_tokens"], "key": case["expect"].get("cause") or case["expect"]["status"],
                     **j})
    done = [r for r in rows if "error" not in r]
    return {"script": script, "rows": rows, "summary": {
        "cases": len(rows), "errors": len(rows) - len(done), "equal_to_key": sum(r["equal"] for r in done),
        "wrong_cause_kept": sum(r["wrong_cause_kept"] for r in done), "bad_edit_kept": sum(r["bad_edit_kept"] for r in done),
        "downgraded": sum(1 for r in done if r["downgraded"]), "model_calls": sum(r["calls"] for r in done),
        "input_tokens": sum(r["input_tokens"] for r in done), "output_tokens": sum(r["output_tokens"] for r in done)}}


def confusion(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    done = [r for r in rows if "error" not in r and r["key"] in CAUSES]
    matrix: Dict[str, Counter] = {c: Counter() for c in CAUSES}
    for r in done:
        matrix[r["key"]][r["cause"] or r["status"]] += 1
    acc = {c: (sum(1 for r in done if r["key"] == c and r["cause"] == c), sum(1 for r in done if r["key"] == c)) for c in CAUSES}
    return {"matrix": {k: dict(v) for k, v in matrix.items() if v}, "accuracy": {k: v for k, v in acc.items() if v[1]}}


def markdown(report: Dict[str, Any]) -> str:
    out = [f"# Testbed diagnose run ({report['model']})", "", f"Service: {report['service']}. Wall time {report['seconds']} s.", "",
           "| script | cases | equal to the key | wrong cause kept | edit on a non-edit cause | downgraded | model calls | tokens in / out |",
           "|---|---|---|---|---|---|---|---|"]
    for r in report["runs"]:
        s = r["summary"]
        out.append(f"| {r['script']} | {s['cases']} | {s['equal_to_key']} | {s['wrong_cause_kept']} | {s['bad_edit_kept']} | "
                   f"{s['downgraded']} | {s['model_calls']} | {s['input_tokens']} / {s['output_tokens']} |")
    out += ["", "## Cases", "", "| case | key | " + " | ".join(r["script"] for r in report["runs"]) + " |",
            "|---|---|" + "---|" * len(report["runs"])]
    for i, case in enumerate(report["cases"]):
        cells = []
        for r in report["runs"]:
            row = r["rows"][i]
            cells.append(row["error"] if "error" in row else
                         f"{row['cause'] or row['status']}{' (' + row['downgraded'] + ')' if row['downgraded'] else ''}"
                         f"{' KEPT WRONG' if row['wrong_cause_kept'] else ''}")
        out.append(f"| {case['id']} | {case['expect'].get('cause') or case['expect']['status']} | " + " | ".join(cells) + " |")
    if "confusion" in report:
        out += ["", "## Confusion (information only)", "", "```", json.dumps(report["confusion"], indent=1), "```"]
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run the diagnose cases through POST /v1/diagnose and report per script.")
    ap.add_argument("--model", choices=["fake", "env"], default="fake",
                    help="fake: scripted answers, offline; env: the service's HEAL_TEXT_* model (information only)")
    ap.add_argument("--script", nargs="*", default=list(SCRIPTS), choices=SCRIPTS, help="(fake) the scripted models to run")
    ap.add_argument("--cases", nargs="*", default=None, help="only these case ids")
    ap.add_argument("--timeout-ms", type=int, default=1500, help="action timeout of the suite (default 1500)")
    ap.add_argument("--out", default=str(HERE / ".runs" / "diagnose"), help="directory for diagnose.json and diagnose.md")
    args = ap.parse_args(argv)
    cases = [c for c in json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"] if not args.cases or c["id"] in args.cases]
    svc = ra.Service(args.model, None)
    svc.core.settings = dataclasses.replace(svc.core.settings, diagnose_enabled=True)
    model = Scripted() if args.model == "fake" else None
    if model is not None:
        svc.core.model = model
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    probe_path = out_dir / "probe.js"
    probe_path.write_text(svc.get("/v1/probe.js"), encoding="utf-8")
    server, root = ra.serve_sites()
    started = time.time()
    try:
        captured = {c["id"]: capture(c, root, probe_path, args.timeout_ms) for c in cases}
    finally:
        server.shutdown()
    report: Dict[str, Any] = {"model": args.model, "service": svc.label, "cases": cases, "runs": []}
    for script in (args.script if model is not None else ["env"]):
        run = run_script(svc, model, script, cases, captured, real=model is None)
        report["runs"].append(run)
        s = run["summary"]
        print(f"{script:13} cases {s['cases']} equal_to_key {s['equal_to_key']} wrong_cause_kept {s['wrong_cause_kept']} "
              f"bad_edit_kept {s['bad_edit_kept']} downgraded {s['downgraded']} calls {s['model_calls']} "
              f"tokens {s['input_tokens']}/{s['output_tokens']}" + (f" errors {s['errors']}" if s["errors"] else ""), flush=True)
    if model is None:
        report["confusion"] = confusion(report["runs"][0]["rows"])
        print("accuracy by cause (correct/asked, information only):",
              {k: f"{a}/{n}" for k, (a, n) in report["confusion"]["accuracy"].items()})
    report["seconds"] = round(time.time() - started, 1)
    (out_dir / "diagnose.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "diagnose.md").write_text(markdown(report), encoding="utf-8")
    print(f"\nwrote {out_dir / 'diagnose.json'} and {out_dir / 'diagnose.md'}")
    bad = sum(r["summary"]["errors"] for r in report["runs"])
    if model is not None:
        for r in report["runs"]:
            s = r["summary"]
            bad += s["wrong_cause_kept"] + s["bad_edit_kept"]
            if r["script"] == "oracle":
                bad += s["cases"] - s["errors"] - s["equal_to_key"]
    print("diagnose:", "OK" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
