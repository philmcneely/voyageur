#!/usr/bin/env python3
"""
===============================================================================
Bench Report (acceptance.json files of several model arms, turned into one comparison)
===============================================================================
testbed/bench.py runs the acceptance run once per arm and repeat and hands the acceptance.json files to this module, which holds
no state and starts no process: `build` makes the comparison (a plain dict, the JSON report) and `markdown` prints it. It is its own
module so the numbers and the layout are tested from fixture files, with no browser and no model.

Every number has the definition docs/RESULTS.md gives it. Per arm: healed, needs_human (and by reason), wrong heals kept (a healed
step whose element is not the golden one), wrong element caught (a candidate the client rejected in the page), steps run against
the golden steps, model calls, input and output tokens, cost (the service's own figure from HEAL_MODEL_PRICES, null when any model
used has no price), heal latency p50 and p95 (the service's start-to-answer time, which includes the client's re-runs of the test)
and the model-call latency p50 and p95. With repeats, each number is shown as min, max and mean over the repeats.

The agreement view lists, for every golden step where the arms differ (or one arm differed from itself across repeats), the pick of
each arm: the locator it wrote, or its needs_human reason, and whether that matches the golden file. A fit line per arm states two
facts and recommends nothing: the wrong heals it kept, and its healed count as a share of the scripted ceiling (the `fake` arm).

Features:
    ✓ run_summary / arm_summary: the numbers of one run and their min, max and mean over repeats; cost is null unless every run is priced
    ✓ agreement: per golden step, the pick of each arm and how it ended against the golden file (ok, miss, wrong); only differing
      steps are listed
    ✓ fit: wrong heals kept and healed as a share of the scripted ceiling, as facts
    ✓ markdown: header line (date, version, commit, specs, client), the settings every arm shared, one column per arm with the
      scripted ceiling first; cells are made safe for a table (pipes, newlines)
    ✓ An arm whose runs failed is reported as failed, never dropped

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

CEILING = "fake"                      # the scripted arm every bench includes: what the pipeline gives when the pick is right
NOT_REACHED = "not reached"
SETTING_LABELS = (("max_tokens", "max tokens"), ("candidate_cap", "candidate cap"), ("max_calls", "max calls"),
                  ("timeout_s", "model timeout (s)"), ("action_timeout_ms", "action timeout (ms)"), ("thinking", "thinking"),
                  ("fingerprints", "fingerprints"), ("vision", "vision"))
NUMBERS = ("healed", "needs_human", "not_drift", "wrong_kept", "wrong_caught", "steps_run", "steps_golden", "steps_matching_golden",
           "model_calls", "input_tokens", "output_tokens")
LATENCIES = ("heal_ms_p50", "heal_ms_p95", "model_ms_p50", "model_ms_p95")


def _percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def _stat(values: Sequence[float]) -> Dict[str, Any]:
    return {"min": min(values), "max": max(values), "mean": sum(values) / len(values), "values": list(values)}


# ---- one step ----------------------------------------------------------------------------------------------------------------
def label_of(row: Mapping[str, Any]) -> str:
    """What an arm did at a step: the locator it wrote, or its needs_human reason."""
    if row["status"] == "healed":
        return row.get("final_locator") or "healed"
    return f"{row['status']}: {row['reason']}" if row.get("reason") else str(row["status"])


def verdict_of(row: Mapping[str, Any]) -> Optional[str]:
    """How a step ended against the golden file: `ok` (a heal on the golden element, or a needs_human where the element is gone),
    `miss` (no heal where the golden file has an element), `wrong` (a heal on another element, or a heal where the element is gone).
    None for a step the golden file does not list."""
    want = (row.get("expected") or {}).get("outcome")
    if want in (None, "unlisted"):
        return None
    if row["status"] == "healed":
        return "ok" if want == "healed" and row.get("on_target") else "wrong"
    return "ok" if row["status"] == "needs_human" and want == "needs_human" else "miss"


def matches_golden(row: Mapping[str, Any]) -> Optional[bool]:
    """Whether the step ended as the golden file says; None for a step the golden file does not list."""
    verdict = verdict_of(row)
    return None if verdict is None else verdict == "ok"


def _step_key(variant: Mapping[str, Any], row: Mapping[str, Any]) -> Tuple[Any, ...]:
    index = row.get("step_index")
    return (variant.get("spec"), variant["variant"], row["test"], index if index is not None else ("line", row.get("line")))


# ---- one run -----------------------------------------------------------------------------------------------------------------
def run_summary(acceptance: Mapping[str, Any]) -> Dict[str, Any]:
    """The numbers of one acceptance.json, summed over its variants."""
    variants = acceptance["variants"]
    rows = [r for v in variants for r in v["rows"]]
    sums = [v["summary"] for v in variants]
    reasons = Counter(r.get("reason") or "unknown" for r in rows if r["status"] == "needs_human")
    durations = [r["duration_ms"] for r in rows if r.get("duration_ms") is not None]
    service = acceptance.get("service_metrics") or {}
    latency = service.get("latency") or {}
    return {
        "healed": sum(1 for r in rows if r["status"] == "healed"),
        "needs_human": sum(1 for r in rows if r["status"] == "needs_human"),
        "not_drift": sum(1 for r in rows if r["status"] == "not_drift"),
        "needs_human_by_reason": dict(reasons),
        "wrong_kept": sum(s["wrong_kept"] for s in sums), "wrong_caught": sum(s["wrong_catches"] for s in sums),
        "steps_run": sum(s["steps_run"] for s in sums), "steps_golden": sum(s["steps_expected"] for s in sums),
        "steps_matching_golden": sum(1 for r in rows if matches_golden(r)),
        "model_calls": sum(s["model_calls"] for s in sums), "input_tokens": sum(s["input_tokens"] for s in sums),
        "output_tokens": sum(s["output_tokens"] for s in sums), "cost": service.get("cost"),
        "heal_ms_p50": _percentile(durations, 0.5), "heal_ms_p95": _percentile(durations, 0.95),
        "model_ms_p50": latency.get("model_ms_p50"), "model_ms_p95": latency.get("model_ms_p95")}


def arm_summary(arm: Mapping[str, Any]) -> Dict[str, Any]:
    """The numbers of an arm over its repeats: min, max, mean and the value of each repeat. Cost is null when any repeat has none."""
    runs = [run_summary(r) for r in arm["runs"]]
    if not runs:
        return {"repeats": 0}
    out: Dict[str, Any] = {"repeats": len(runs)}
    for key in NUMBERS:
        out[key] = _stat([r[key] for r in runs])
    for key in ("cost",) + LATENCIES:
        values = [r[key] for r in runs]
        out[key] = None if any(v is None for v in values) else _stat(values)
    reasons = sorted({k for r in runs for k in r["needs_human_by_reason"]})
    out["needs_human_by_reason"] = {k: _stat([r["needs_human_by_reason"].get(k, 0) for r in runs]) for k in reasons}
    return out


# ---- the agreement view ------------------------------------------------------------------------------------------------------
def agreement(arms: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Per golden step, the pick of each arm over its repeats, and the steps where the arms differ. A step is listed when two arms
    picked differently, or when one arm's repeats did not all pick the same."""
    steps: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    picks: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    live = [a for a in arms if a["runs"]]
    for arm in live:
        for n, acceptance in enumerate(arm["runs"]):
            for variant in acceptance["variants"]:
                for row in variant["rows"]:
                    key = _step_key(variant, row)
                    info = steps.setdefault(key, {"spec": variant.get("spec"), "variant": variant["variant"], "test": row["test"],
                                                  "line": row.get("line"), "statement": row.get("statement", ""),
                                                  "golden": row.get("golden_target"),
                                                  "golden_outcome": (row.get("expected") or {}).get("outcome")})
                    if info["golden"] is None and row.get("golden_target"):
                        info["golden"] = row["golden_target"]
                    cell = picks.setdefault(key, {}).setdefault(arm["name"], {"votes": Counter(), "match": {}, "seen": set()})
                    cell["votes"][label_of(row)] += 1
                    cell["match"].setdefault(label_of(row), verdict_of(row))
                    cell["seen"].add(n)
    differ: List[Dict[str, Any]] = []
    for key, info in steps.items():
        cells: Dict[str, Dict[str, Any]] = {}
        for arm in live:
            cell = picks[key].get(arm["name"])
            repeats = len(arm["runs"])
            votes = Counter(cell["votes"]) if cell else Counter()
            if repeats - len(cell["seen"] if cell else ()) > 0:
                votes[NOT_REACHED] += repeats - len(cell["seen"] if cell else ())
            top = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            kind = cell["match"].get(top) if cell else None
            cells[arm["name"]] = {"label": top, "votes": dict(votes), "stable": len(votes) == 1, "verdict": kind,
                                  "matches_golden": None if kind is None else kind == "ok"}
        if len({c["label"] for c in cells.values()}) > 1 or any(not c["stable"] for c in cells.values()):
            differ.append({**info, "picks": cells})
    return {"steps_total": len(steps), "differ": differ}


# ---- the fit lines -----------------------------------------------------------------------------------------------------------
def _num(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def _range(stat: Optional[Mapping[str, Any]], money: bool = False) -> str:
    """A stat as one cell: the value when every repeat agrees, else `min to max (mean m)`."""
    if stat is None:
        return "n/a"
    show = _money if money else _num
    if stat["min"] == stat["max"]:
        return show(stat["min"])
    return f"{show(stat['min'])} to {show(stat['max'])} (mean {show(stat['mean'])})"


def _money(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"{x:.2f}" if x >= 1 else (f"{x:.4f}".rstrip("0") if x else "0")


def fit(arms: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One line per arm but the scripted ceiling: its wrong heals kept and its healed count as a share of the ceiling's. Facts only."""
    ceiling = next((a for a in arms if a["name"] == CEILING and a.get("summary", {}).get("repeats")), None)
    ceiling_healed = ceiling["summary"]["healed"]["mean"] if ceiling else None
    out = []
    for arm in arms:
        if arm["name"] == CEILING:
            continue
        s = arm["summary"]
        if not s.get("repeats"):
            out.append({"arm": arm["name"], "wrong_kept": None, "healed": None, "share_of_ceiling": None,
                        "line": f"{arm['name']}: no result ({arm.get('error') or 'no run completed'})"})
            continue
        healed, share = s["healed"]["mean"], None
        if ceiling_healed:
            share = healed / ceiling_healed
        pct = "n/a" if share is None else f"{share * 100:.0f}%"
        line = (f"{arm['name']}: wrong heals kept: {_range(s['wrong_kept'])}; healed {_num(healed)} of the {_num(ceiling_healed)} the "
                f"scripted ceiling healed ({pct}); needs_human {_num(s['needs_human']['mean'])}; wrong element caught "
                f"{_range(s['wrong_caught'])}")
        if s["repeats"] > 1:
            line += f" (healed and needs_human are the mean of {s['repeats']} repeats)"
        if arm.get("error"):
            line += f" (stopped early: {arm['error']})"
        out.append({"arm": arm["name"], "wrong_kept": s["wrong_kept"]["mean"], "healed": healed, "share_of_ceiling": share, "line": line})
    return out


# ---- the report --------------------------------------------------------------------------------------------------------------
def build(header: Mapping[str, Any], shared: Mapping[str, Any], arms: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The comparison as a plain dict. `arms` are {name, kind, model, provider, base_url, api_key_env, settings (what the arm sets
    for itself), runs (acceptance.json dicts), error?}; the scripted ceiling is first."""
    ordered = sorted(arms, key=lambda a: a["name"] != CEILING)           # stable: the ceiling first, the rest as given
    described = []
    for a in ordered:
        item = {k: a.get(k) for k in ("name", "kind", "model", "provider", "base_url", "api_key_env", "settings", "error",
                                      "extra_body_keys")}
        item["summary"] = arm_summary(a)
        described.append(item)
    return {"header": dict(header), "shared_settings": dict(shared), "arms": described, "agreement": agreement(ordered),
            "fit": fit(described)}


def _safe(text: Any) -> str:
    """Text that came from a page or a model (a locator, a reason, a test name) made inert in markdown: no tag, link or image can
    form from it, so a viewer that renders the file cannot be made to fetch or show anything."""
    out = str(text).replace("\\", "\\\\").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for char in "[]`":
        out = out.replace(char, "\\" + char)
    return out


def _cell(text: Any) -> str:
    """One table cell: a pipe or a line break in it cannot end the cell or the row."""
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _table(head: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[str]:
    return ["| " + " | ".join(_cell(h) for h in head) + " |", "|" + "---|" * len(head)] + \
           ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]


def markdown(bench: Mapping[str, Any]) -> str:
    h, arms = bench["header"], bench["arms"]
    out = ["# Model bench", "",
           f"{h['date']} · voyageur {h['version']} · commit {h['commit']} · specs {' '.join(h['specs'])} · client {h['client']} · "
           f"repeat {h.get('repeat', 1)}", "",
           "## Settings, the same for every arm", ""]
    shared = bench["shared_settings"]
    out += _table(["setting", "value"], [(label, shared.get(key, "")) for key, label in SETTING_LABELS if key in shared])
    odd = [(a["name"], a.get("settings") or {}) for a in arms if a.get("settings")]
    if odd:
        labels = dict(SETTING_LABELS)
        out += ["", "Settings that differ between arms: " + "; ".join(
            f"{name}: " + ", ".join(f"{labels.get(k, k)} {v}" for k, v in sorted(s.items())) for name, s in odd) + "."]
    out += ["", "## Arms", ""]
    out += _table(["arm", "kind", "model", "provider", "base url", "api key from", "extra body keys"],
                  [(_safe(a["name"]), a["kind"], _safe(a.get("model") or ""), a.get("provider") or "", _safe(a.get("base_url") or ""),
                    a.get("api_key_env") or "", _safe(", ".join(a.get("extra_body_keys") or []))) for a in arms])
    failed = [a for a in arms if a.get("error") or not a["summary"].get("repeats")]
    if failed:
        out += [""] + [f"- {_safe(a['name'])} failed: {_safe(a.get('error') or 'no run completed')}" for a in failed]
    out += ["", "## Results", ""]
    names = [a["name"] for a in arms]
    reasons = sorted({k for a in arms for k in a["summary"].get("needs_human_by_reason", {})})

    def line(label: str, key: str, money: bool = False, reason: Optional[str] = None) -> List[str]:
        cells = []
        for a in arms:
            s = a["summary"]
            if not s.get("repeats"):
                cells.append("failed")
            elif reason is not None:
                cells.append(_range(s["needs_human_by_reason"].get(reason) or {"min": 0, "max": 0, "mean": 0}))
            else:
                cells.append(_range(s[key], money))
        return [label] + cells
    rows = [line("healed", "healed"), line("needs_human", "needs_human")]
    rows += [line(f"needs_human: {r}", "", reason=r) for r in reasons]
    rows += [line("not drift", "not_drift"), line("wrong heals kept", "wrong_kept"), line("wrong element caught", "wrong_caught")]
    steps = ["steps run / golden"]
    for a in arms:
        s = a["summary"]
        steps.append("failed" if not s.get("repeats") else f"{_range(s['steps_run'])} / {_range(s['steps_golden'])}")
    rows += [steps, line("steps matching the golden file", "steps_matching_golden"), line("model calls", "model_calls"),
             line("input tokens", "input_tokens"), line("output tokens", "output_tokens"), line("cost", "cost", money=True),
             line("heal latency p50 (ms)", "heal_ms_p50"), line("heal latency p95 (ms)", "heal_ms_p95"),
             line("model call latency p50 (ms)", "model_ms_p50"), line("model call latency p95 (ms)", "model_ms_p95")]
    out += _table(["metric"] + names, rows)
    out += ["", "Heal latency is the service's time from the start of a heal to its answer, which includes the client's re-runs of the "
            "test between tries; model call latency is the model's own. Cost is the service's figure from HEAL_MODEL_PRICES and is n/a "
            "when a model used has no price; the scripted arm is never priced. With repeats a cell is `min to max (mean m)`.",
            "", "## Fit", ""] + [f"- {_safe(f['line'])}" for f in bench["fit"]]
    agree = bench["agreement"]
    out += ["", "## Where the arms differ", ""]
    if not agree["differ"]:
        out += [f"The arms agree on every step ({agree['steps_total']} steps)."]
    else:
        out += [f"{len(agree['differ'])} of {agree['steps_total']} steps differ between the arms or between repeats of one arm. "
                "Steps where all arms agree are not listed. `ok`: the step ended as the golden file says. `MISS`: no heal "
                "where the golden file has an element. `WRONG`: a heal on another element, or a heal where the element is gone.", ""]
        rows = []
        for s in agree["differ"]:
            cells = []
            for name in names:
                c = s["picks"].get(name)
                if c is None:
                    cells.append("failed")
                    continue
                mark = {"ok": "ok", "miss": "MISS", "wrong": "WRONG", None: "-"}[c["verdict"]]
                text = f"{_safe(c['label'])} [{mark}]"
                if not c["stable"]:
                    text += " (" + ", ".join(f"{_safe(k)} x{v}" for k, v in sorted(c["votes"].items(), key=lambda kv: (-kv[1], kv[0]))) + ")"
                cells.append(text)
            golden = _safe(s["golden"]) if s["golden"] is not None else ("(element gone)" if s["golden_outcome"] == "needs_human" else "")
            rows.append([_safe(f"{s['variant']} {s['test']}:{s['line']}"), golden] + cells)
        out += _table(["step", "golden"] + names, rows)
    return "\n".join(out) + "\n"
