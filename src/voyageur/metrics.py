"""
===============================================================================
Metrics (the numbers a PR body or a dashboard may claim)
===============================================================================
Every figure is computed from the heal record and nothing else, with one definition each, so a number printed at the end of
a run or shown on a dashboard can be traced to the heals behind it: the same filters feed GET /v1/heals, and `drill` names
the filter string that lists the heals behind each number. A heal counts as healed only when the record holds a verify
(count 1, the right element, the exact test passed); that rule is applied where the record is written, so this module only
counts.

Definitions (the spec's headline table). Advise-mode requests (mode `advise`) are reported on their own under `advise` and
are left out of every rate below, so asking for advice never dilutes the success rate; so are diagnoses (mode `diagnose`, reported under
`diagnose`: a failure that is not locator drift has nothing to heal):
    failures_seen         heals started (each is one failing test a client sent)
    locator_drift         failures_seen minus not_drift
    success_rate          healed / locator_drift            fail_rate  needs_human / locator_drift (split by reason)
    tests_fixed           sum of `covers` over healed heals (a shared page-object fix counts every test it covered)
    locators_fixed        distinct source locators (file + call text) among healed heals
    model_calls           rows of the calls table;  calls_per_fixed_test = model_calls / tests_fixed
    calls_that_led_to_fix calls belonging to healed heals not later edited, reverted or closed unmerged, and a share of all calls
    tokens_per_fix        (input + output tokens) / tests_fixed;  cost only when every model used has a price
    kept_rate             healed heals merged unchanged / healed heals whose PR has closed (merged unchanged, edited, reverted
                          or closed unmerged)
    durability            healed heals merged unchanged that have lived 10 runs and 30 days, of which the share that never
                          failed again (heals still younger than that are `pending`)
    escaped_wrong_heals   healed heals a human edited or reverted: the target is 0
    wrong_element_catches candidates verify stopped for landing on the wrong element before they reached a PR
    outcomes_by_model     the same outcomes split by the model that made a call in the heal (below)

Outcomes by model (`outcomes_by_model`, one row per model, for comparing models over the same kind of failures): a heal belongs to
every model that made a call in it, so a heal that used a text model and a vision model is counted once under each and the rows
add up to more than the heals (`outcomes_by_model_note` says so). A row holds the heals, `healed`, `verified_heals` (healed, and
the exact test went green after the patch; the rest of healed were kept because the re-run failed only on a later statement),
`needs_human` and its reasons, the wrong-element catches, and the tokens and cost of that model's own calls (`cost` is None
without a price for it). Heals that made no model call (a fingerprint match, a triage answer) are in `outcomes_without_model`.
The `model` filter keeps the heals in which any call used that model, with all of their calls (so the totals above count the other
models' calls in those heals), and `outcomes_by_model` then holds that model's row alone.

A figure with nothing to divide by is None, never 0 and never an error.

Features:
    ✓ aggregate(): the headline numbers plus the breakdowns behind them (outcome mix and per-run mix, needs-human reasons,
      settled-at and ended-at step, tier movement, hot spots, cost by role and by model with local and cloud shares, budget,
      latency of heals and model calls, vision, cache, language, branches, follow-up counts)
    ✓ `diagnose`: requests, answers by status, causes and confidences, edits suggested, heals advised to revert, answers the service
      downgraded, model calls and tokens; each with a drill link, and none of them in a heal rate
    ✓ `drill`: for each number, the query string for GET /v1/heals that lists the heals behind it
    ✓ `outcomes_by_model`: heals, verified heals, needs_human by reason, wrong-element catches, tokens and cost per model, each
      number with a drill link (`drill.by_model`); heals with no model call are reported apart
    ✓ Optional filters: repo, framework, language, page language, run, model, time window

Environment Variables:
    None (prices per model come from config.ServiceSettings.prices, HEAL_MODEL_PRICES).

Author: PMAC
===============================================================================
"""
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlencode

from .store import BAD_PR_STATES, DURABLE_DAYS, DURABLE_RUNS, MAX_AGG_ROWS, Store

CLOSED_PR_STATES = ("merged_unchanged", "edited", "reverted", "closed_unmerged")
MAX_RUNS = 30


def _ratio(n: float, d: float) -> Optional[float]:
    return n / d if d else None


def _percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def _q(**params: Any) -> str:
    return urlencode({k: v for k, v in params.items() if v is not None})


def _cost(rows: Mapping[Any, Dict[str, int]], model_of: Any, prices: Mapping[str, Tuple[float, float]]) -> Optional[float]:
    """Cost of the token rows when every model in them has a price, else None."""
    if not rows or not all(model_of(k) in prices for k in rows):
        return None
    return sum(r["input_tokens"] * prices[model_of(k)][0] + r["output_tokens"] * prices[model_of(k)][1]
               for k, r in rows.items()) / 1e6


def aggregate(store: Store, prices: Optional[Mapping[str, Tuple[float, float]]] = None, since: Optional[float] = None,
              until: Optional[float] = None, max_rows: int = MAX_AGG_ROWS, **filters: Any) -> Dict[str, Any]:
    """The aggregates for the heals matching `filters` (repo, framework, language, page_language, run_id) in the window.
    At most the newest `max_rows` heals are read (and their calls and tries); `truncated` says when more matched."""
    prices = prices or {}
    every = store.heals_in(since, until, max_rows=max_rows, **filters)
    advice = [h for h in every if (h.get("mode") or "heal") == "advise"]
    diagnoses = [h for h in every if h.get("mode") == "diagnose"]
    heals = [h for h in every if (h.get("mode") or "heal") not in ("advise", "diagnose")]
    all_calls = store.calls_in(since, until, max_rows=max_rows, **filters)
    calls = [c for c in all_calls if c["mode"] not in ("advise", "diagnose")]
    tries = store.tries_in(since, until, max_rows=max_rows, **filters)
    apart = {h["heal_id"] for h in advice + diagnoses}
    tries = [t for t in tries if t["heal_id"] not in apart]
    healed = [h for h in heals if h["outcome"] == "healed"]
    human = [h for h in heals if h["outcome"] == "needs_human"]
    drift = sum(1 for h in heals if h["drift"])
    fixed = sum(h["covers"] or 1 for h in healed)
    closed = [h for h in healed if h["pr_state"] in CLOSED_PR_STATES]
    merged = [h for h in healed if h["pr_state"] == "merged_unchanged"]
    bad = {h["heal_id"] for h in healed if h["pr_state"] in BAD_PR_STATES}
    escaped = [h for h in healed if h["pr_state"] in ("edited", "reverted")]
    good = {h["heal_id"] for h in healed} - bad
    led = sum(1 for c in calls if c["heal_id"] in good)
    due = [h for h in merged if (h["runs_since"] or 0) >= DURABLE_RUNS and (h["days_since"] or 0) >= DURABLE_DAYS]
    durable = [h for h in due if not h["failed_again"]]
    broke = [h for h in healed if h["failed_again"]]
    pending = [h for h in healed if h["followup_at"] is not None and not h["failed_again"] and h not in due]
    tokens_in, tokens_out = sum(c["input_tokens"] for c in calls), sum(c["output_tokens"] for c in calls)
    tokens = tokens_in + tokens_out
    local_tokens = sum(c["input_tokens"] + c["output_tokens"] for c in calls if c["local"])
    by_model: Dict[Tuple[str, str, int], Dict[str, int]] = {}
    by_role: Dict[str, Dict[str, int]] = {}
    role_models: Dict[Tuple[str, str], Dict[str, int]] = {}      # (role, model) whether local or cloud: the cost of a role
    for c in calls:
        pair = role_models.setdefault((c["role"], c["model"]), {"input_tokens": 0, "output_tokens": 0})
        pair["input_tokens"] += c["input_tokens"]
        pair["output_tokens"] += c["output_tokens"]
        row = by_model.setdefault((c["role"], c["model"], int(bool(c["local"]))),
                                  {"calls": 0, "input_tokens": 0, "output_tokens": 0, "images": 0})
        role = by_role.setdefault(c["role"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "images": 0,
                                              "local_tokens": 0, "cloud_tokens": 0})
        for target in (row, role):
            target["calls"] += 1
            target["input_tokens"] += c["input_tokens"]
            target["output_tokens"] += c["output_tokens"]
            target["images"] += c["images"]
        role["local_tokens" if c["local"] else "cloud_tokens"] += c["input_tokens"] + c["output_tokens"]
    cost = _cost(by_model, lambda k: k[1], prices)
    spots: Dict[Tuple[str, str], Dict[str, int]] = {}
    for h in heals:
        if h["locator_key"] and h["drift"]:
            spot = spots.setdefault((h["file"], h["locator_key"]), {"heals": 0, "healed": 0})
            spot["heals"] += 1
            spot["healed"] += h["outcome"] == "healed"
    hot = sorted(spots.items(), key=lambda kv: (-kv[1]["heals"], kv[0]))[:10]
    durations = [h["duration_ms"] for h in heals if h["outcome"] != "try" and h["duration_ms"] is not None]
    call_ms = [c["latency_ms"] for c in calls if c["latency_ms"] is not None]
    step_ms: Dict[str, List[float]] = {}
    for c in calls:
        if c["latency_ms"] is not None:
            step_ms.setdefault(str(c["step"]), []).append(c["latency_ms"])
    vision_calls = [c for c in calls if c["role"] == "vision"]
    cached = sum(1 for h in heals if h["cache_hit"])
    runs: Dict[str, Dict[str, Any]] = {}
    for h in heals:                                   # oldest first: a run is listed at its first heal
        run = runs.setdefault(h["run_id"] or "default", {"run_id": h["run_id"] or "default", "first_at": h["created_at"],
                                                         "last_at": h["created_at"], "heals": 0, "healed": 0,
                                                         "needs_human": 0, "not_drift": 0, "open": 0, "tokens": 0})
        run["last_at"] = h["created_at"]
        run["heals"] += 1
        key = h["outcome"] if h["outcome"] in ("healed", "needs_human", "not_drift") else "open"
        run[key] += 1
    for c in calls:
        run = runs.get(c["run_id"] or "default")
        if run is not None:
            run["tokens"] += c["input_tokens"] + c["output_tokens"]
    run_rows = sorted(runs.values(), key=lambda r: -r["last_at"])[:MAX_RUNS]
    advice_calls = [c for c in all_calls if c["mode"] == "advise"]
    diagnose_calls = [c for c in all_calls if c["mode"] == "diagnose"]
    answered = [h for h in diagnoses if h["outcome"] == "diagnosed"]
    causes, confidences = Counter(h["reason"] for h in answered), Counter(h["confidence"] for h in answered if h["confidence"])
    reasons = Counter(h["reason"] or "unknown" for h in human)
    settled = Counter(str(h["settled_at"]) for h in healed if h["settled_at"])
    outcomes = Counter(h["outcome"] for h in heals if h["outcome"])
    movement = {k: sum(1 for h in healed if h["tier_movement"] == k) for k in ("up", "same", "down")}
    languages = Counter(h["page_language"] for h in healed if h["page_language"])
    frameworks = Counter(h["framework"] for h in heals if h["framework"])
    client_languages = Counter(h["language"] for h in heals if h["language"])
    models_of: Dict[str, set] = {}                     # heal id -> the models that made a call in it
    per_model: Dict[str, Dict[str, int]] = {}
    for c in calls:
        models_of.setdefault(c["heal_id"], set()).add(c["model"])
        mine_calls = per_model.setdefault(c["model"], {"model_calls": 0, "input_tokens": 0, "output_tokens": 0})
        mine_calls["model_calls"] += 1
        mine_calls["input_tokens"] += c["input_tokens"]
        mine_calls["output_tokens"] += c["output_tokens"]
    heals_of: Dict[str, List[Dict[str, Any]]] = {m: [] for m in per_model}
    no_model: List[Dict[str, Any]] = []
    for h in heals:
        used = models_of.get(h["heal_id"])
        for m in used or ():
            heals_of[m].append(h)
        if not used:
            no_model.append(h)
    by_model_rows = []
    for m in sorted(heals_of):
        if filters.get("model") not in (None, m):          # a model filter answers for that model: another row's number would
            continue                                       # count only the heals it shared with it, and its link would list more
        mine = heals_of[m]
        mine_healed = [h for h in mine if h["outcome"] == "healed"]
        mine_reasons = Counter(h["reason"] or "unknown" for h in mine if h["outcome"] == "needs_human")
        by_model_rows.append({
            "model": m, "heals": len(mine), "healed": len(mine_healed),
            "verified_heals": sum(1 for h in mine_healed if h["verify_passed"]),
            "needs_human": sum(mine_reasons.values()), "needs_human_by_reason": dict(mine_reasons),
            "success_rate": _ratio(len(mine_healed), sum(1 for h in mine if h["drift"])),
            "wrong_element_catches": sum(h["wrong_element"] or 0 for h in mine),
            "heals_with_wrong_element": sum(1 for h in mine if h["wrong_element"]),
            **per_model[m], "cost": _cost({m: per_model[m]}, lambda k: k, prices)})
    return {
        "truncated": len(every) >= max_rows,
        "failures_seen": len(heals), "locator_drift": drift, "not_drift": len(heals) - drift,
        "healed": len(healed), "needs_human": len(human), "open": sum(1 for h in heals if h["outcome"] == "try"),
        "success_rate": _ratio(len(healed), drift), "fail_rate": _ratio(len(human), drift),
        "needs_human_by_reason": dict(reasons),
        "tests_fixed": fixed, "locators_fixed": len({h["locator_key"] for h in healed if h["locator_key"]}),
        "model_calls": len(calls), "calls_per_fixed_test": _ratio(len(calls), fixed),
        "calls_that_led_to_fix": led, "calls_that_led_to_fix_rate": _ratio(led, len(calls)),
        "input_tokens": tokens_in, "output_tokens": tokens_out, "tokens_per_fix": _ratio(tokens, fixed),
        "cost": cost, "cost_per_fix": None if cost is None else _ratio(cost, fixed),
        "tokens_per_kept_heal": _ratio(tokens, len(merged)),
        "kept_rate": _ratio(len(merged), len(closed)), "kept": len(merged), "pr_closed": len(closed),
        "pr_state": dict(Counter(h["pr_state"] for h in healed if h["pr_state"])),
        "escaped_wrong_heals": len(escaped), "escaped_or_edited": len(bad),
        "durability": {"rate": _ratio(len(durable), len(due)), "eligible": len(due), "held": len(durable),
                       "pending": len(pending), "broke_again": len(broke), "runs": DURABLE_RUNS, "days": DURABLE_DAYS},
        "outcome_mix": dict(outcomes),
        "runs": run_rows,
        "settled_at": dict(settled),
        "ended_at": dict(Counter(str(h["ended_at"]) for h in heals if h["ended_at"])),
        "tier_movement": movement,
        "hot_spots": [{"file": f, "locator_key": k, **v} for (f, k), v in hot],
        "cost_by_model": [{"role": r, "model": m, "local": bool(loc), **v,
                           "cost": _cost({(r, m): v}, lambda k: k[1], prices)}
                          for (r, m, loc), v in sorted(by_model.items())],
        "cost_by_role": [{"role": r, **v, "cost": _cost({k: x for k, x in role_models.items() if k[0] == r},
                                                        lambda k: k[1], prices)} for r, v in sorted(by_role.items())],
        "outcomes_by_model": by_model_rows,
        "outcomes_by_model_note": "a heal that used two models (a text pick and a vision pick) is counted under each",
        "outcomes_without_model": {"heals": len(no_model), "healed": sum(1 for h in no_model if h["outcome"] == "healed"),
                                   "needs_human": sum(1 for h in no_model if h["outcome"] == "needs_human")},
        "local_token_share": _ratio(local_tokens, tokens), "cloud_token_share": _ratio(tokens - local_tokens, tokens),
        "budget_exhausted": sum(1 for h in heals if h["budget_exhausted"]),
        "latency": {"heal_ms_p50": _percentile(durations, 0.5), "heal_ms_p95": _percentile(durations, 0.95),
                    "heal_ms_max": max(durations) if durations else None,
                    "model_ms_avg": _ratio(sum(call_ms), len(call_ms)), "model_ms_p50": _percentile(call_ms, 0.5),
                    "model_ms_p95": _percentile(call_ms, 0.95),
                    "model_ms_by_step": {k: _ratio(sum(v), len(v)) for k, v in sorted(step_ms.items())}},
        "vision": {"calls": len(vision_calls), "heals": len({c["heal_id"] for c in vision_calls})},
        "cache": {"hits": cached, "rate": _ratio(cached, len(heals))},
        "language": dict(languages), "client_language": dict(client_languages), "framework": dict(frameworks),
        "branches_added": sum(1 for h in healed if h["branch"]),
        "wrong_element_catches": sum(h["wrong_element"] or 0 for h in heals),
        "heals_with_wrong_element": sum(1 for h in heals if h["wrong_element"]),
        "rejected_candidates": sum(1 for t in tries if t["result"] == "wrong_element"),
        "broke_again": len(broke),
        "advise": {"requests": len(advice), "advised": sum(1 for h in advice if h["outcome"] == "advised"),
                   "needs_human": sum(1 for h in advice if h["outcome"] == "needs_human"),
                   "not_drift": sum(1 for h in advice if h["outcome"] == "not_drift"),
                   "model_calls": len(advice_calls),
                   "input_tokens": sum(c["input_tokens"] for c in advice_calls),
                   "output_tokens": sum(c["output_tokens"] for c in advice_calls)},
        "diagnose": {"requests": len(diagnoses), "diagnosed": len(answered),
                     "needs_human": sum(1 for h in diagnoses if h["outcome"] == "needs_human"),
                     "not_applicable": sum(1 for h in diagnoses if h["outcome"] == "not_applicable"),
                     "by_cause": dict(causes), "by_confidence": dict(confidences),
                     "patches_suggested": sum(1 for h in answered if h["patch_suggested"]),
                     "revert_advised": causes.get("earlier_heal_wrong_element", 0),
                     "downgraded": sum(1 for h in answered if h["downgraded"]), "model_calls": len(diagnose_calls),
                     "input_tokens": sum(c["input_tokens"] for c in diagnose_calls),
                     "output_tokens": sum(c["output_tokens"] for c in diagnose_calls)},
        "drill": {
            "failures_seen": _q(mode="heal"), "locator_drift": _q(mode="heal", drift=1),
            "not_drift": _q(mode="heal", outcome="not_drift"), "healed": _q(mode="heal", outcome="healed"),
            "needs_human": _q(mode="heal", outcome="needs_human"), "open": _q(mode="heal", outcome="try"),
            "success_rate": _q(mode="heal", outcome="healed"), "fail_rate": _q(mode="heal", outcome="needs_human"),
            "tests_fixed": _q(mode="heal", outcome="healed"), "locators_fixed": _q(mode="heal", outcome="healed"),
            "model_calls": _q(mode="heal", with_calls=1),
            "calls_per_fixed_test": _q(mode="heal", with_calls=1),
            "calls_that_led_to_fix": _q(mode="heal", with_calls=1, led_to_fix=1),
            "calls_that_led_to_fix_rate": _q(mode="heal", with_calls=1),
            "tokens_per_fix": _q(mode="heal", with_calls=1), "cost_per_fix": _q(mode="heal", with_calls=1),
            "tokens_per_kept_heal": _q(mode="heal", pr_state="merged_unchanged"),
            "kept_rate": _q(mode="heal", pr_state="merged_unchanged"),
            "pr_closed": _q(mode="heal", pr_state=",".join(CLOSED_PR_STATES)),
            "escaped_wrong_heals": _q(mode="heal", pr_state="edited,reverted"),
            "durability": _q(mode="heal", durability="pass"), "durability_failed": _q(mode="heal", durability="fail"),
            "durability_pending": _q(mode="heal", durability="pending"),
            "wrong_element_catches": _q(mode="heal", wrong_element=1),
            "budget_exhausted": _q(mode="heal", budget_exhausted=1), "branches_added": _q(mode="heal", outcome="healed",
                                                                                         branch=1),
            "cache": _q(mode="heal", cache_hit=1), "vision": _q(mode="heal", role="vision"),
            "advise": _q(mode="advise"), "latency": _q(mode="heal", sort="slowest"),
            "diagnose": _q(mode="diagnose"), "diagnose_diagnosed": _q(mode="diagnose", outcome="diagnosed"),
            "diagnose_needs_human": _q(mode="diagnose", outcome="needs_human"),
            "diagnose_not_applicable": _q(mode="diagnose", outcome="not_applicable"),
            "diagnose_by_cause": {c: _q(mode="diagnose", outcome="diagnosed", reason=c) for c in causes},
            "diagnose_by_confidence": {c: _q(mode="diagnose", confidence=c) for c in confidences},
            "diagnose_patches_suggested": _q(mode="diagnose", patch_suggested=1),
            "diagnose_revert_advised": _q(mode="diagnose", outcome="diagnosed", reason="earlier_heal_wrong_element"),
            "diagnose_downgraded": _q(mode="diagnose", downgraded=1), "diagnose_model_calls": _q(mode="diagnose", with_calls=1),
            "needs_human_by_reason": {r: _q(mode="heal", outcome="needs_human", reason=r) for r in reasons},
            "outcome_mix": {o: _q(mode="heal", outcome=o) for o in outcomes},
            "settled_at": {s: _q(mode="heal", outcome="healed", settled_at=s) for s in settled},
            "tier_movement": {m: _q(mode="heal", outcome="healed", tier_movement=m) for m in movement},
            "language": {lang: _q(mode="heal", outcome="healed", page_language=lang) for lang in languages},
            "client_language": {lang: _q(mode="heal", language=lang) for lang in client_languages},
            "framework": {fw: _q(mode="heal", framework=fw) for fw in frameworks},
            "pr_state": {p: _q(mode="heal", outcome="healed", pr_state=p) for p in
                         Counter(h["pr_state"] for h in healed if h["pr_state"])},
            "runs": {r["run_id"]: _q(mode="heal", run_id=r["run_id"]) for r in run_rows},
            "hot_spots": {f"{f}|{k}": _q(mode="heal", file=f, locator_key=k) for (f, k), _ in hot},
            "cost_by_model": {f"{r}|{m}": _q(mode="heal", role=r, model=m) for (r, m, _), _v in by_model.items()},
            "cost_by_role": {r: _q(mode="heal", role=r) for r in by_role},
            "by_model": {r["model"]: {
                "heals": _q(mode="heal", model=r["model"]),
                "healed": _q(mode="heal", model=r["model"], outcome="healed"),
                "verified_heals": _q(mode="heal", model=r["model"], outcome="healed", verify_passed=1),
                "needs_human": _q(mode="heal", model=r["model"], outcome="needs_human"),
                "needs_human_by_reason": {reason: _q(mode="heal", model=r["model"], outcome="needs_human", reason=reason)
                                          for reason in r["needs_human_by_reason"]},
                "wrong_element_catches": _q(mode="heal", model=r["model"], wrong_element=1),
                "model_calls": _q(mode="heal", model=r["model"])} for r in by_model_rows},
            "outcomes_without_model": _q(mode="heal", with_calls=0),
            "cost_local": _q(mode="heal", local=1), "cost_cloud": _q(mode="heal", local=0, with_calls=1),
        },
    }
