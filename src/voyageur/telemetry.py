"""
===============================================================================
Telemetry (Prometheus text exposition, hand-written)
===============================================================================
Counters and histograms the service keeps in memory as events happen, rendered in the Prometheus text format (version
0.0.4) by GET /metrics. They are process-lifetime counters, the way Prometheus expects them: a restart resets them to zero
and `rate()` copes. They do not read the audit store, so they work with HEAL_RETENTION_DAYS=0 and never go down when old
records are purged. The same events are in the store with their detail (GET /v1/metrics and /v1/heals); these are for a
Grafana board and alerts.

    voyageur_heals_total{outcome,mode}             heals that reached a final answer
    voyageur_needs_human_total{reason}             needs_human answers by reason code
    voyageur_heal_duration_seconds{mode}           histogram, start to final answer
    voyageur_model_calls_total{role,model,local,ok}
    voyageur_model_tokens_total{role,model,direction}   direction is input or output
    voyageur_model_images_total{role,model}
    voyageur_model_call_duration_seconds{role,model}    histogram
    voyageur_model_heals_total{model,outcome}      heals (not advise or diagnose) that reached a final answer, by each model that made a call
                                                   in them: a heal that used two models counts under both; no call, no series
    voyageur_model_needs_human_total{model,reason} the needs_human ones of those, by reason code
    voyageur_model_wrong_element_total{model}      wrong-element catches in those heals
    voyageur_cache_hits_total                      heals served by the decision cache
    voyageur_wrong_element_total                   candidates that landed on the wrong element and were caught
    voyageur_followups_total{pr_state}             post-PR reports
    voyageur_followup_failed_again_total           reports that the healed locator failed again
    voyageur_audits_total                          POST /v1/audit requests answered
    voyageur_overlays_total                        POST /v1/overlay requests answered
    voyageur_diagnoses_total{cause,confidence}     diagnoses answered (POST /v1/diagnose), by the cause named and its confidence
    voyageur_info{version}, voyageur_open_conversations   gauges

Features:
    ✓ No dependency: counters and fixed-bucket histograms behind one lock
    ✓ Label values are escaped (backslash, quote, newline) and come only from fixed codes or the operator's own model names;
      nothing from a request (file text, page copy, locators, a test name) is ever a label
    ✓ render() is deterministic (sorted), so a scrape and a test see the same text

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import threading
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)
HELP = {
    "heals_total": ("counter", "Heals that reached a final answer, by outcome and mode"),
    "needs_human_total": ("counter", "needs_human answers by reason code"),
    "heal_duration_seconds": ("histogram", "Seconds from the start of a heal to its final answer"),
    "model_calls_total": ("counter", "Model calls by role, model, local or cloud, and whether they returned text"),
    "model_tokens_total": ("counter", "Model tokens by role, model and direction"),
    "model_images_total": ("counter", "Images sent to a model"),
    "model_call_duration_seconds": ("histogram", "Seconds one model call took"),
    "model_heals_total": ("counter", "Heals that reached a final answer, by outcome and each model that made a call in them"),
    "model_needs_human_total": ("counter", "needs_human answers by reason code and each model that made a call in the heal"),
    "model_wrong_element_total": ("counter", "Wrong-element catches by each model that made a call in the heal"),
    "cache_hits_total": ("counter", "Heals served by the decision cache"),
    "wrong_element_total": ("counter", "Candidates that landed on the wrong element and were caught by verify"),
    "followups_total": ("counter", "Post-PR reports by PR state"),
    "followup_failed_again_total": ("counter", "Post-PR reports that the healed locator failed again"),
    "audits_total": ("counter", "Locator audits answered"),
    "overlays_total": ("counter", "Overlays (locators marked on a screenshot, no model) answered"),
    "diagnoses_total": ("counter", "Diagnoses answered, by cause and confidence"),
}
Labels = Tuple[Tuple[str, str], ...]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(labels: Labels, extra: Iterable[Tuple[str, str]] = ()) -> str:
    pairs = list(labels) + list(extra)
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in pairs) + "}" if pairs else ""


def _key(labels: Mapping[str, object]) -> Labels:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


class Telemetry:
    """The in-memory counters and histograms. Safe to share between threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[Tuple[str, Labels], float] = {}
        self._hists: Dict[Tuple[str, Labels], Tuple[List[int], float, int]] = {}

    def inc(self, name: str, by: float = 1, **labels: object) -> None:
        with self._lock:
            key = (name, _key(labels))
            self._counters[key] = self._counters.get(key, 0.0) + by

    def observe(self, name: str, value: float, **labels: object) -> None:
        with self._lock:
            key = (name, _key(labels))
            counts, total, n = self._hists.get(key) or ([0] * len(DURATION_BUCKETS), 0.0, 0)
            counts = [c + (1 if value <= b else 0) for c, b in zip(counts, DURATION_BUCKETS)]
            self._hists[key] = (counts, total + value, n + 1)

    # ---- what the core reports ---------------------------------------------------------------------------------
    def heal(self, outcome: str, mode: str, reason: str, seconds: float, cache_hit: bool, wrong_element: int,
             models: Iterable[str] = ()) -> None:
        self.inc("heals_total", outcome=outcome, mode=mode)
        if outcome == "needs_human":
            self.inc("needs_human_total", reason=reason or "unknown")
        for model in models if mode == "heal" else ():            # each model that made a call in this heal
            self.inc("model_heals_total", model=model, outcome=outcome)
            if outcome == "needs_human":
                self.inc("model_needs_human_total", model=model, reason=reason or "unknown")
            if wrong_element:
                self.inc("model_wrong_element_total", wrong_element, model=model)
        self.observe("heal_duration_seconds", seconds, mode=mode)
        if cache_hit:
            self.inc("cache_hits_total")
        if wrong_element:
            self.inc("wrong_element_total", wrong_element)

    def call(self, role: str, model: str, local: bool, ok: bool, input_tokens: int, output_tokens: int, images: int,
             seconds: float) -> None:
        self.inc("model_calls_total", role=role, model=model, local=str(local).lower(), ok=str(ok).lower())
        self.inc("model_tokens_total", input_tokens, role=role, model=model, direction="input")
        self.inc("model_tokens_total", output_tokens, role=role, model=model, direction="output")
        if images:
            self.inc("model_images_total", images, role=role, model=model)
        self.observe("model_call_duration_seconds", seconds, role=role, model=model)

    def followup(self, pr_state: Optional[str], failed_again: bool) -> None:
        if pr_state:
            self.inc("followups_total", pr_state=pr_state)
        if failed_again:
            self.inc("followup_failed_again_total")

    # ---- exposition --------------------------------------------------------------------------------------------
    def render(self, version: str = "", open_conversations: int = 0) -> str:
        """The Prometheus text exposition (0.0.4) of everything counted so far."""
        with self._lock:
            counters, hists = dict(self._counters), dict(self._hists)
        lines: List[str] = []
        for name, (kind, help_text) in HELP.items():
            lines += [f"# HELP voyageur_{name} {help_text}", f"# TYPE voyageur_{name} {kind}"]
            if kind == "counter":
                series = sorted((labels, v) for (n, labels), v in counters.items() if n == name)
                if not series:
                    series = [((), 0.0)] if name in ("cache_hits_total", "wrong_element_total", "audits_total",
                                                     "overlays_total", "followup_failed_again_total") else []
                lines += [f"voyageur_{name}{_labels(labels)} {_num(v)}" for labels, v in series]
            else:
                for (n, labels), (counts, total, count) in sorted((k, v) for k, v in hists.items() if k[0] == name):
                    for bound, c in zip(DURATION_BUCKETS, counts):
                        lines.append(f"voyageur_{name}_bucket{_labels(labels, [('le', _num(bound))])} {c}")
                    lines.append(f"voyageur_{name}_bucket{_labels(labels, [('le', '+Inf')])} {count}")
                    lines.append(f"voyageur_{name}_sum{_labels(labels)} {_num(total)}")
                    lines.append(f"voyageur_{name}_count{_labels(labels)} {count}")
        lines += ["# HELP voyageur_info The service version", "# TYPE voyageur_info gauge",
                  f'voyageur_info{{version="{_escape(version)}"}} 1',
                  "# HELP voyageur_open_conversations Heals waiting for the client's result",
                  "# TYPE voyageur_open_conversations gauge", f"voyageur_open_conversations {open_conversations}"]
        return "\n".join(lines) + "\n"


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))
