"""
===============================================================================
Rollup (the counts a purged heal leaves behind)
===============================================================================
A heal record is purged after HEAL_RETENTION_DAYS, but the totals on /v1/metrics and the dashboard are for the life of the
service. Before the store deletes a heal it turns that heal, its model calls and its tries into a handful of additive
counters and adds them to a daily table (`rollup`). metrics.aggregate() adds those counters to the ones it computes from the
records still kept, so a total does not drop on the day its heals are purged.

A counter is a key `(metric, k1, k2, k3)` and a number. The table adds the day (UTC), the scope (all heals, or the heals that
used one model, so the `model` filter keeps working) and four dimensions (repo, framework, client language, page language).
Counts only, and a short code or a name the operator chose is all a key can hold:
    metric      a fixed name from this module ("heals", "healed", "cm_calls", ...)
    k1 k2 k3    an outcome, a reason code, a step number, a PR state, a language, a role, or the name of a model

No file contents, page text, locator, test name, path, run id, error text or reason in a model's words is ever a key. `repo` is the
name the client's run reported and is kept as a dimension because the dashboard filters on it.

What the rollup cannot hold, because it is not a sum: the distinct locators fixed, the hot spots, the per-run mix, the latency
percentiles. Those are computed from the records still kept, and `archive.retained_only` in /v1/metrics says so. A heal's
durability verdict is counted at the moment the heal is rolled up (due, held, broke again, still pending): the heal itself
cannot be opened afterwards.

Features:
    ✓ heal_counts(): one function turns one heal, its calls and its tries into the counters, the same for every purge, so a
      heal is counted by one definition whether it is read live or from the rollup
    ✓ dims_of(): a client picks the repo, framework and language names and the rollup is never purged, so a name is cut to 100
      characters, one that could carry a credential is filed under "other" (a character a credential or a URL needs, @ : ? # = or
      a control character; or a shape of a token, redact.looks_like_credential: a ghp_, github_pat_, sk-, xox or AKIA key, a JWT, a
      long mixed-case run or hex digest), a dimension holds at most 200 distinct values and the four together at most 500
      combinations; the rest are filed under "other" and still counted
    ✓ A day older than HEAL_ROLLUP_DETAIL_DAYS is folded (store.py): its repo, framework and client-language dimensions are
      dropped and its rows added together, so the table stops growing with the names clients choose. Every counter, the model
      scope and the page language stay, so a total reads the same before and after
    ✓ The framework breakdown is read from the framework dimension, not stored as a counter of its own, so a free-text name
      is never a counter key
    ✓ scopes_of(): the scopes a heal is counted under: every heal under "", and under each model that made a call in it
    ✓ The metrics are named once here, in the order metrics.aggregate() reads them

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from .redact import looks_like_credential

BAD_PR_STATES = ("edited", "reverted", "closed_unmerged")      # a heal a human did not keep as written
CLOSED_PR_STATES = ("merged_unchanged", "edited", "reverted", "closed_unmerged")
DURABLE_RUNS, DURABLE_DAYS = 10, 30                           # "still passing 10 runs and 30 days later"
DAY = 86400
# what /v1/metrics reads from the records still kept, because it cannot be added up across heals
RETAINED_ONLY = ("locators_fixed", "hot_spots", "runs", "latency")
# the filters a rollup row can answer: the dimensions it keeps, and the model (a scope of its own)
DIMENSIONS = ("repo", "framework", "language", "page_language")
# dimensions a fold drops (page_language is a short list of language codes and stays, so its filter still answers for every day)
FOLDED_DIMENSIONS = ("repo", "framework", "language")
# distinct values per dimension, distinct combinations of the four, a name's length, and the name overflow is filed under
MAX_DIM_VALUES, MAX_DIM_COMBOS, MAX_DIM_LEN, OTHER = 200, 500, 100, "other"
_PLAIN = re.compile(r"[A-Za-z0-9._/ +-]*")                    # a name that can carry no credential (no @, :, ?, #, = or control character)
Key = Tuple[str, str, str, str]


def _k(metric: str, k1: Any = "", k2: Any = "", k3: Any = "") -> Key:
    return (metric, str(k1), str(k2), str(k3))


def dims_of(heal: Mapping[str, Any], known: Dict[str, set]) -> Tuple[str, str, str, str]:
    """The dimension values a heal is filed under ("" for a field the record does not hold). A client chooses these names and the
    rollup is never purged, so a name is cut to MAX_DIM_LEN characters, one with a character a credential or a URL query needs
    (@ : ? # =) or the shape of a token (redact.looks_like_credential) is filed under "other", a dimension holds at most MAX_DIM_VALUES distinct values and all four together at most
    MAX_DIM_COMBOS distinct combinations (`known` holds what is stored so far and this call adds to it); past either, the heal is
    still counted, under "other"."""
    out = []
    for d in DIMENSIONS:
        value = str(heal.get(d) or "")[:MAX_DIM_LEN]
        if (not _PLAIN.fullmatch(value) or looks_like_credential(value)
                or (value and value not in known[d] and len(known[d]) >= MAX_DIM_VALUES)):
            value = OTHER
        out.append(value)
    combo = tuple(out)
    if combo not in known["combos"] and len(known["combos"]) >= MAX_DIM_COMBOS:
        combo = (OTHER,) * len(DIMENSIONS)
    for d, value in zip(DIMENSIONS, combo):
        known[d].add(value)
    known["combos"].add(combo)
    return combo                                                        # type: ignore[return-value]


def day_of(epoch: float) -> int:
    return int(epoch // DAY)


def scopes_of(calls: Iterable[Mapping[str, Any]]) -> List[str]:
    """Every heal is counted under "" (all heals) and under each model that made a call in it."""
    return [""] + sorted({str(c["model"]) for c in calls if c.get("model")})


def heal_counts(h: Mapping[str, Any], calls: List[Mapping[str, Any]], tries: List[Mapping[str, Any]]) -> "Counter[Key]":
    """The counters of one heal. Advise and diagnose requests have counters of their own and are in none of the rates, the
    way metrics.aggregate() keeps them apart."""
    c: "Counter[Key]" = Counter()
    c[_k("archived")] += 1
    mode = h.get("mode") or "heal"
    tok_in, tok_out = sum(x["input_tokens"] or 0 for x in calls), sum(x["output_tokens"] or 0 for x in calls)
    outcome = h.get("outcome")
    if mode == "advise":
        c[_k("adv_requests")] += 1
        c[_k("adv_advised")] += outcome == "advised"
        c[_k("adv_human")] += outcome == "needs_human"
        c[_k("adv_not_drift")] += outcome == "not_drift"
        c[_k("adv_calls")] += len(calls)
        c[_k("adv_in")] += tok_in
        c[_k("adv_out")] += tok_out
        return c
    if mode == "diagnose":
        c[_k("dg_requests")] += 1
        c[_k("dg_human")] += outcome == "needs_human"
        c[_k("dg_na")] += outcome == "not_applicable"
        if outcome == "diagnosed":
            c[_k("dg_diagnosed")] += 1
            c[_k("dg_cause", h.get("reason") or "")] += 1
            if h.get("confidence"):
                c[_k("dg_conf", h["confidence"])] += 1
            c[_k("dg_patches")] += bool(h.get("patch_suggested"))
            c[_k("dg_downgraded")] += bool(h.get("downgraded"))
        c[_k("dg_calls")] += len(calls)
        c[_k("dg_in")] += tok_in
        c[_k("dg_out")] += tok_out
        return c

    healed, human = outcome == "healed", outcome == "needs_human"
    pr = h.get("pr_state")
    c[_k("heals")] += 1
    c[_k("drift")] += bool(h.get("drift"))
    c[_k("healed")] += healed
    c[_k("human")] += human
    c[_k("open")] += outcome == "try"
    if outcome:
        c[_k("outcome", outcome)] += 1
    if h.get("ended_at"):
        c[_k("ended", h["ended_at"])] += 1
    if h.get("language"):
        c[_k("client_language", h["language"])] += 1
    c[_k("cached")] += bool(h.get("cache_hit"))
    c[_k("budget")] += bool(h.get("budget_exhausted"))
    c[_k("wrong")] += h.get("wrong_element") or 0
    c[_k("wrong_heals")] += bool(h.get("wrong_element"))
    c[_k("rejected")] += sum(1 for t in tries if t["result"] == "wrong_element")
    if human:
        c[_k("reason", h.get("reason") or "unknown")] += 1
    if healed:
        c[_k("fixed")] += h.get("covers") or 1
        c[_k("branches")] += bool(h.get("branch"))
        if h.get("settled_at"):
            c[_k("settled", h["settled_at"])] += 1
        if h.get("tier_movement") in ("up", "same", "down"):
            c[_k("movement", h["tier_movement"])] += 1
        if h.get("page_language"):
            c[_k("page_language", h["page_language"])] += 1
        if pr:
            c[_k("pr_state", pr)] += 1
        closed, merged = pr in CLOSED_PR_STATES, pr == "merged_unchanged"
        c[_k("closed")] += closed
        c[_k("merged")] += merged
        c[_k("bad")] += pr in BAD_PR_STATES
        c[_k("escaped")] += pr in ("edited", "reverted")
        due = merged and (h.get("runs_since") or 0) >= DURABLE_RUNS and (h.get("days_since") or 0) >= DURABLE_DAYS
        c[_k("due")] += due
        c[_k("durable")] += bool(due and not h.get("failed_again"))
        c[_k("broke")] += bool(h.get("failed_again"))
        c[_k("pending")] += bool(h.get("followup_at") is not None and not h.get("failed_again") and not due)
        if pr not in BAD_PR_STATES:
            c[_k("led")] += len(calls)
    c[_k("calls")] += len(calls)
    c[_k("in")] += tok_in
    c[_k("out")] += tok_out
    for x in calls:
        tokens = (x["input_tokens"] or 0) + (x["output_tokens"] or 0)
        where = (x["role"], x["model"], int(bool(x["local"])))
        c[_k("cm_calls", *where)] += 1
        c[_k("cm_in", *where)] += x["input_tokens"] or 0
        c[_k("cm_out", *where)] += x["output_tokens"] or 0
        c[_k("cm_img", *where)] += x["images"] or 0
        c[_k("local_tok")] += tokens if x["local"] else 0
        c[_k("vision_calls")] += x["role"] == "vision"
    c[_k("vision_heals")] += any(x["role"] == "vision" for x in calls)
    models = sorted({str(x["model"]) for x in calls if x["model"]})
    for m in models:
        c[_k("mh_heals", m)] += 1
        c[_k("mh_healed", m)] += healed
        c[_k("mh_verified", m)] += bool(healed and h.get("verify_passed"))
        c[_k("mh_drift", m)] += bool(h.get("drift"))
        c[_k("mh_wrong", m)] += h.get("wrong_element") or 0
        c[_k("mh_wrong_heals", m)] += bool(h.get("wrong_element"))
        if human:
            c[_k("mh_reason", m, h.get("reason") or "unknown")] += 1
    if not models:
        c[_k("nm_heals")] += 1
        c[_k("nm_healed")] += healed
        c[_k("nm_human")] += human
    return Counter({k: int(v) for k, v in c.items() if v})


def merge(into: Dict[Tuple, int], counts: Mapping[Key, int], prefix: Tuple) -> None:
    """Add `counts` to `into`, each key filed under `prefix` (day, scope and the four dimensions)."""
    for key, n in counts.items():
        into[prefix + key] = into.get(prefix + key, 0) + n
