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
    ✓ Totals outlive the records: the counts of heals purged after HEAL_RETENTION_DAYS (rollup.py) are added to every sum, count
      and rate, by-model rows and breakdowns included, and `archive` says how many heals that is. Computed from the records still
      kept, because they are not sums: locators_fixed, hot_spots, runs, latency (`archive.retained_only`). A run filter, or a
      filter on anything the rollup does not keep (outcome, file), reads the records kept and `archive.included` is false
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

from .rollup import CLOSED_PR_STATES, RETAINED_ONLY, Key
from .store import BAD_PR_STATES, DURABLE_DAYS, DURABLE_RUNS, MAX_AGG_ROWS, Store

MAX_RUNS = 30


class _Archive:
    """The counters of heals purged since (store.rollup_counts), for the same filters and window as the live read. It answers
    for the filters a rollup row keeps (repo, framework, language, page language, model); with any other filter set (a run, an
    outcome, a file) it holds nothing and `info()` says it was not included."""

    def __init__(self, store: Store, since: Optional[float], until: Optional[float], filters: Dict[str, Any]):
        self.store, self.since, self.until, self.filters = store, since, until, filters
        self.by_dimension: Dict[str, Dict[str, int]] = {}
        self.included = store.rollup_answers(filters)
        self.counts: Mapping[Key, int] = store.rollup_counts(since, until, **filters) if self.included else {}

    def a(self, metric: str, k1: Any = "", k2: Any = "", k3: Any = "") -> int:
        """One counter (0 when the archive holds none)."""
        return int(self.counts.get((metric, str(k1), str(k2), str(k3)), 0))

    def group(self, metric: str) -> Dict[str, int]:
        """The counters of a metric by its first key."""
        return {k[1]: n for k, n in self.counts.items() if k[0] == metric}

    def pairs(self, metric: str) -> Dict[Tuple[str, str], int]:
        """The counters of a metric by its first two keys."""
        return {(k[1], k[2]): n for k, n in self.counts.items() if k[0] == metric}

    def keyed(self, metric: str) -> Dict[Tuple[str, str, str], int]:
        """The counters of a metric by all three keys."""
        return {(k[1], k[2], k[3]): n for k, n in self.counts.items() if k[0] == metric}

    def counter(self, metric: str, live: "Counter[Any]") -> "Counter[Any]":
        """`live` (a count by name over the records kept) plus the archive's count of the same metric."""
        out: "Counter[Any]" = Counter(live)
        for name, n in self.group(metric).items():
            out[name] += n
        return out

    def counter_by_dimension(self, dimension: str, live: "Counter[Any]") -> "Counter[Any]":
        """`live` plus the archived heals by the value of a dimension the rollup keeps (a free-text name is never a counter key)."""
        out: "Counter[Any]" = Counter(live)
        for name, n in self.by_dimension.get(dimension, self._dimension(dimension)).items():
            out[name] += n
        return out

    def _dimension(self, dimension: str) -> Dict[str, int]:
        return self.store.rollup_by_dimension(dimension, self.since, self.until, **self.filters) if self.included else {}

    def info(self) -> Dict[str, Any]:
        """What the archive holds, for the answer: how many purged heals it counted, the span they cover, and the figures that
        are computed from the records still kept because they are not sums."""
        span = self.store.rollup_info()
        return {"included": self.included, "heals": self.a("archived"), "from": span["from"], "through": span["through"],
                "retained_only": list(RETAINED_ONLY)}


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
    A = _Archive(store, since, until, filters)             # the counts of heals purged since, added to every total below
    healed = [h for h in heals if h["outcome"] == "healed"]
    human = [h for h in heals if h["outcome"] == "needs_human"]
    drift = sum(1 for h in heals if h["drift"]) + A.a("drift")
    fixed = sum(h["covers"] or 1 for h in healed) + A.a("fixed")
    closed = [h for h in healed if h["pr_state"] in CLOSED_PR_STATES]
    merged = [h for h in healed if h["pr_state"] == "merged_unchanged"]
    bad = {h["heal_id"] for h in healed if h["pr_state"] in BAD_PR_STATES}
    escaped = [h for h in healed if h["pr_state"] in ("edited", "reverted")]
    good = {h["heal_id"] for h in healed} - bad
    led = sum(1 for c in calls if c["heal_id"] in good) + A.a("led")
    due = [h for h in merged if (h["runs_since"] or 0) >= DURABLE_RUNS and (h["days_since"] or 0) >= DURABLE_DAYS]
    durable = [h for h in due if not h["failed_again"]]
    broke = [h for h in healed if h["failed_again"]]
    pending = [h for h in healed if h["followup_at"] is not None and not h["failed_again"] and h not in due]
    n_heals, n_healed, n_human = len(heals) + A.a("heals"), len(healed) + A.a("healed"), len(human) + A.a("human")
    n_merged, n_closed, n_bad, n_escaped = (len(merged) + A.a("merged"), len(closed) + A.a("closed"), len(bad) + A.a("bad"),
                                            len(escaped) + A.a("escaped"))
    n_due, n_durable, n_broke, n_pending = (len(due) + A.a("due"), len(durable) + A.a("durable"), len(broke) + A.a("broke"),
                                            len(pending) + A.a("pending"))
    n_calls = len(calls) + A.a("calls")
    tokens_in = sum(c["input_tokens"] for c in calls) + A.a("in")
    tokens_out = sum(c["output_tokens"] for c in calls) + A.a("out")
    tokens = tokens_in + tokens_out
    local_tokens = sum(c["input_tokens"] + c["output_tokens"] for c in calls if c["local"]) + A.a("local_tok")
    by_model: Dict[Tuple[str, str, int], Dict[str, int]] = {}
    by_role: Dict[str, Dict[str, int]] = {}
    role_models: Dict[Tuple[str, str], Dict[str, int]] = {}      # (role, model) whether local or cloud: the cost of a role
    per_model: Dict[str, Dict[str, int]] = {}

    def add_calls(role: str, model: str, local: int, n: int, tin: int, tout: int, images: int) -> None:
        """Count n calls of one (role, model, local) into the rows by model, by role, by role and model, and per model."""
        pair = role_models.setdefault((role, model), {"input_tokens": 0, "output_tokens": 0})
        pair["input_tokens"] += tin
        pair["output_tokens"] += tout
        row = by_model.setdefault((role, model, local), {"calls": 0, "input_tokens": 0, "output_tokens": 0, "images": 0})
        by_r = by_role.setdefault(role, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "images": 0,
                                         "local_tokens": 0, "cloud_tokens": 0})
        for target in (row, by_r):
            target["calls"] += n
            target["input_tokens"] += tin
            target["output_tokens"] += tout
            target["images"] += images
        by_r["local_tokens" if local else "cloud_tokens"] += tin + tout
        mine_calls = per_model.setdefault(model, {"model_calls": 0, "input_tokens": 0, "output_tokens": 0})
        mine_calls["model_calls"] += n
        mine_calls["input_tokens"] += tin
        mine_calls["output_tokens"] += tout
    for c in calls:
        add_calls(c["role"], c["model"], int(bool(c["local"])), 1, c["input_tokens"], c["output_tokens"], c["images"])
    for (role, model, local), n in A.keyed("cm_calls").items():
        add_calls(role, model, int(local), n, A.a("cm_in", role, model, local), A.a("cm_out", role, model, local),
                  A.a("cm_img", role, model, local))
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
    cached = sum(1 for h in heals if h["cache_hit"]) + A.a("cached")
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
    causes = A.counter("dg_cause", Counter(h["reason"] for h in answered))
    confidences = A.counter("dg_conf", Counter(h["confidence"] for h in answered if h["confidence"]))
    reasons = A.counter("reason", Counter(h["reason"] or "unknown" for h in human))
    settled = A.counter("settled", Counter(str(h["settled_at"]) for h in healed if h["settled_at"]))
    outcomes = A.counter("outcome", Counter(h["outcome"] for h in heals if h["outcome"]))
    movement = {k: sum(1 for h in healed if h["tier_movement"] == k) + A.a("movement", k) for k in ("up", "same", "down")}
    languages = A.counter("page_language", Counter(h["page_language"] for h in healed if h["page_language"]))
    frameworks = A.counter_by_dimension("framework", Counter(h["framework"] for h in heals if h["framework"]))
    client_languages = A.counter("client_language", Counter(h["language"] for h in heals if h["language"]))
    pr_states = A.counter("pr_state", Counter(h["pr_state"] for h in healed if h["pr_state"]))
    ended = A.counter("ended", Counter(str(h["ended_at"]) for h in heals if h["ended_at"]))
    models_of: Dict[str, set] = {}                     # heal id -> the models that made a call in it
    for c in calls:
        models_of.setdefault(c["heal_id"], set()).add(c["model"])
    stat: Dict[str, Dict[str, Any]] = {}               # per model: the heals it took part in, counted
    no_model = {"heals": A.a("nm_heals"), "healed": A.a("nm_healed"), "needs_human": A.a("nm_human")}

    def model_row(m: str) -> Dict[str, Any]:
        return stat.setdefault(m, {"heals": 0, "healed": 0, "verified": 0, "drift": 0, "reasons": Counter(), "wrong": 0,
                                   "wrong_heals": 0})
    for h in heals:
        used = models_of.get(h["heal_id"])
        for m in used or ():
            r = model_row(m)
            r["heals"] += 1
            r["healed"] += h["outcome"] == "healed"
            r["verified"] += h["outcome"] == "healed" and bool(h["verify_passed"])
            r["drift"] += bool(h["drift"])
            r["wrong"] += h["wrong_element"] or 0
            r["wrong_heals"] += bool(h["wrong_element"])
            if h["outcome"] == "needs_human":
                r["reasons"][h["reason"] or "unknown"] += 1
        if not used:
            no_model["heals"] += 1
            no_model["healed"] += h["outcome"] == "healed"
            no_model["needs_human"] += h["outcome"] == "needs_human"
    for m, n in A.group("mh_heals").items():
        r = model_row(m)
        r["heals"] += n
        r["healed"] += A.a("mh_healed", m)
        r["verified"] += A.a("mh_verified", m)
        r["drift"] += A.a("mh_drift", m)
        r["wrong"] += A.a("mh_wrong", m)
        r["wrong_heals"] += A.a("mh_wrong_heals", m)
    for (m, reason), n in A.pairs("mh_reason").items():
        model_row(m)["reasons"][reason] += n
    by_model_rows = []
    for m in sorted(stat):
        if filters.get("model") not in (None, m):          # a model filter answers for that model: another row's number would
            continue                                       # count only the heals it shared with it, and its link would list more
        mine = stat[m]
        by_model_rows.append({
            "model": m, "heals": mine["heals"], "healed": mine["healed"], "verified_heals": mine["verified"],
            "needs_human": sum(mine["reasons"].values()), "needs_human_by_reason": dict(mine["reasons"]),
            "success_rate": _ratio(mine["healed"], mine["drift"]),
            "wrong_element_catches": mine["wrong"], "heals_with_wrong_element": mine["wrong_heals"],
            **per_model[m], "cost": _cost({m: per_model[m]}, lambda k: k, prices)})
    return {
        "truncated": len(every) >= max_rows,
        "archive": A.info(),
        "failures_seen": n_heals, "locator_drift": drift, "not_drift": n_heals - drift,
        "healed": n_healed, "needs_human": n_human, "open": sum(1 for h in heals if h["outcome"] == "try") + A.a("open"),
        "success_rate": _ratio(n_healed, drift), "fail_rate": _ratio(n_human, drift),
        "needs_human_by_reason": dict(reasons),
        "tests_fixed": fixed, "locators_fixed": len({h["locator_key"] for h in healed if h["locator_key"]}),
        "model_calls": n_calls, "calls_per_fixed_test": _ratio(n_calls, fixed),
        "calls_that_led_to_fix": led, "calls_that_led_to_fix_rate": _ratio(led, n_calls),
        "input_tokens": tokens_in, "output_tokens": tokens_out, "tokens_per_fix": _ratio(tokens, fixed),
        "cost": cost, "cost_per_fix": None if cost is None else _ratio(cost, fixed),
        "tokens_per_kept_heal": _ratio(tokens, n_merged),
        "kept_rate": _ratio(n_merged, n_closed), "kept": n_merged, "pr_closed": n_closed,
        "pr_state": dict(pr_states),
        "escaped_wrong_heals": n_escaped, "escaped_or_edited": n_bad,
        "durability": {"rate": _ratio(n_durable, n_due), "eligible": n_due, "held": n_durable,
                       "pending": n_pending, "broke_again": n_broke, "runs": DURABLE_RUNS, "days": DURABLE_DAYS},
        "outcome_mix": dict(outcomes),
        "runs": run_rows,
        "settled_at": dict(settled),
        "ended_at": dict(ended),
        "tier_movement": movement,
        "hot_spots": [{"file": f, "locator_key": k, **v} for (f, k), v in hot],
        "cost_by_model": [{"role": r, "model": m, "local": bool(loc), **v,
                           "cost": _cost({(r, m): v}, lambda k: k[1], prices)}
                          for (r, m, loc), v in sorted(by_model.items())],
        "cost_by_role": [{"role": r, **v, "cost": _cost({k: x for k, x in role_models.items() if k[0] == r},
                                                        lambda k: k[1], prices)} for r, v in sorted(by_role.items())],
        "outcomes_by_model": by_model_rows,
        "outcomes_by_model_note": "a heal that used two models (a text pick and a vision pick) is counted under each",
        "outcomes_without_model": no_model,
        "local_token_share": _ratio(local_tokens, tokens), "cloud_token_share": _ratio(tokens - local_tokens, tokens),
        "budget_exhausted": sum(1 for h in heals if h["budget_exhausted"]) + A.a("budget"),
        "latency": {"heal_ms_p50": _percentile(durations, 0.5), "heal_ms_p95": _percentile(durations, 0.95),
                    "heal_ms_max": max(durations) if durations else None,
                    "model_ms_avg": _ratio(sum(call_ms), len(call_ms)), "model_ms_p50": _percentile(call_ms, 0.5),
                    "model_ms_p95": _percentile(call_ms, 0.95),
                    "model_ms_by_step": {k: _ratio(sum(v), len(v)) for k, v in sorted(step_ms.items())}},
        "vision": {"calls": len(vision_calls) + A.a("vision_calls"),
                   "heals": len({c["heal_id"] for c in vision_calls}) + A.a("vision_heals")},
        "cache": {"hits": cached, "rate": _ratio(cached, n_heals)},
        "language": dict(languages), "client_language": dict(client_languages), "framework": dict(frameworks),
        "branches_added": sum(1 for h in healed if h["branch"]) + A.a("branches"),
        "wrong_element_catches": sum(h["wrong_element"] or 0 for h in heals) + A.a("wrong"),
        "heals_with_wrong_element": sum(1 for h in heals if h["wrong_element"]) + A.a("wrong_heals"),
        "rejected_candidates": sum(1 for t in tries if t["result"] == "wrong_element") + A.a("rejected"),
        "broke_again": n_broke,
        "advise": {"requests": len(advice) + A.a("adv_requests"),
                   "advised": sum(1 for h in advice if h["outcome"] == "advised") + A.a("adv_advised"),
                   "needs_human": sum(1 for h in advice if h["outcome"] == "needs_human") + A.a("adv_human"),
                   "not_drift": sum(1 for h in advice if h["outcome"] == "not_drift") + A.a("adv_not_drift"),
                   "model_calls": len(advice_calls) + A.a("adv_calls"),
                   "input_tokens": sum(c["input_tokens"] for c in advice_calls) + A.a("adv_in"),
                   "output_tokens": sum(c["output_tokens"] for c in advice_calls) + A.a("adv_out")},
        "diagnose": {"requests": len(diagnoses) + A.a("dg_requests"), "diagnosed": len(answered) + A.a("dg_diagnosed"),
                     "needs_human": sum(1 for h in diagnoses if h["outcome"] == "needs_human") + A.a("dg_human"),
                     "not_applicable": sum(1 for h in diagnoses if h["outcome"] == "not_applicable") + A.a("dg_na"),
                     "by_cause": dict(causes), "by_confidence": dict(confidences),
                     "patches_suggested": sum(1 for h in answered if h["patch_suggested"]) + A.a("dg_patches"),
                     "revert_advised": causes.get("earlier_heal_wrong_element", 0),
                     "downgraded": sum(1 for h in answered if h["downgraded"]) + A.a("dg_downgraded"),
                     "model_calls": len(diagnose_calls) + A.a("dg_calls"),
                     "input_tokens": sum(c["input_tokens"] for c in diagnose_calls) + A.a("dg_in"),
                     "output_tokens": sum(c["output_tokens"] for c in diagnose_calls) + A.a("dg_out")},
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
            "pr_state": {p: _q(mode="heal", outcome="healed", pr_state=p) for p in pr_states},
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
