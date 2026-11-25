"""
===============================================================================
Heal Session (the ladder, the conversation and the record)
===============================================================================
Runs one heal as a short conversation. `start` climbs a cost ladder and stops at the first step that settles it:
    1 triage (no model): not drift, needs a human, or the one broken call and every other use of the same locator
    2 same-element match (no model): the last passing run's fingerprint against the page inventory
    3 text pick (one model call): the model names one listed element; it never writes locator text
    4 vision pick (one model call, optional): the model names one numbered box on the client's screenshot (see below)
    5 write (no model): the ranked locators of that element, each unique on the page, never below the broken tier unless
      nothing higher is unique
    6 language (no model): a copy-based locator on a page in another language than the passing run gets a branch per language
and answers with a candidate to `try`: a patch the client applies, plus what it should see (one match, this element).
`result` takes what the client saw. Only a count of 1, the right element and a passing exact test make a heal `healed`. Any
other result moves to the next ranked locator for the same element with no model call; a result that hit another element
goes back to the pick with that element ruled out. After max_rounds, or when nothing is left, the heal ends needs_human with
a reason code. With options.mode "advise" the ladder stops after step 6: the answer is the settled element's best locator
(an `advice`, status `advised`) with no patch and no try rounds, under the same gates, budget and cache.

Vision (options.vision): `off` never looks at an image. `auto` runs step 4 only when the text pick fails (no usable pick, or a
pick a gate refused), is a close call (another candidate in the list is a twin of the pick: same tag, role, copy, row and
frame, so the text cannot tell them apart), or the client's live check found it was the wrong element. `on` runs step 4 for
every heal that reaches the pick, after the text pick; a vision pick that passes the gates replaces the text pick, one that
does not leaves it standing. A heal makes at most one vision call, within HEAL_MAX_CALLS. Vision unconfigured, its endpoint
not allowed images, no screenshot sent, or no candidate inside the screenshot: the heal goes on with the text pick and the
record says why, and no image bytes leave. The vision pick is judged by the same gates and identity proof as a text pick.

Cost controls live here: a per-run token budget (HEAL_RUN_TOKEN_BUDGET), a cap on model calls per heal, and a decision cache
keyed on the broken call, the page inventory and the language, so a repeat of the same drift costs nothing and still has to
be verified by the client. Every response carries its usage. Every heal writes one record (store.py).

Features:
    ✓ Statuses: try, healed, needs_human, not_drift; every needs_human carries a reason code and a plain sentence
    ✓ Two step numbers per record: `settled_at` (the step that settled the target element: 2, 3) and `ended_at` (the step
      the heal ended at: 1 triage, 3 pick, 5 write, 6 language, 7 verify)
    ✓ The patch for the broken call plus a patch for each other use of the same locator in the sent files, each with the
      receiver that file uses, each passing the replacement validator
    ✓ Budget: checked before every model call against the run's spend so far; a spent budget ends the heal with
      `budget_exhausted` (never cached)
    ✓ Shared-locator dedup: a decision-cache hit for a repeat of the same drift adds one to the covering heal's `covers`
    ✓ advise mode: status `advised` with the best locator, its tier, tier movement and the other locators written for the
      element; recorded as outcome `advised` with mode `advise`, so rates over real heals are not diluted by it. A pick a
      gate refuses is not advised. A client cannot send a result to an advised heal (409)
    ✓ followup(): what a client saw after the PR (merged unchanged, edited, reverted, open, closed unmerged), whether the
      healed locator failed again, runs and days since; only a heal that ended `healed` takes one
    ✓ audit(): the locators of the sent files ranked against the inventory (audit.py), no model call
    ✓ overlay(): locators marked on the sent screenshot (marks.py), no model call, no record, nothing kept; it shares the audit's
      slots and runs in the killable worker under the request deadline
    ✓ diagnose(): a failure that is not locator drift (triage says not_drift or needs_human) read by one text call that names a
      likely cause, with evidence and at most one suggested edit (diagnose.py). Off unless HEAL_DIAGNOSE is on (DiagnoseOff);
      located drift is `not_applicable`; it makes no try rounds, takes no result, is never cached and never changes the heal
      rates (record mode `diagnose`, outcome `diagnosed`, the cause as the reason); no screenshot is sent
    ✓ Telemetry for GET /metrics is counted here as events happen (telemetry.py)
    ✓ Vision: one overlay and one vision call per heal at most; images counted in usage; the overlay kept with the record
      (store.put_overlay) for HEAL_OVERLAY_RETENTION_DAYS (default 7), the record staying longer; the record's evidence says whether vision ran, why, and whether it changed
      the text pick; the screenshot is held only while a vision call may still need it (bounded: MAX_HELD_IMAGE_BYTES)
    ✓ Logs carry the heal id, the outcome, a reason code and counts only; never file contents, page text or model replies
    ✓ Conversations are held in memory (bounded, oldest dropped); the record is in the store. No lock is held across a
      model call, so heals run concurrently; one result at a time is taken per heal

Environment Variables:
    None here (settings come in through config.ServiceSettings and llm.endpoint_from_env for the text and vision roles).

Author: PMAC
===============================================================================
"""
import hashlib
import ipaddress
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple
from urllib.parse import urlsplit

from . import __version__
from . import stages
from .config import ServiceSettings
from .deadline import Budget, InlineRunner, ProcessRunner, StageFailed
from .failing import norm, printed_of
from . import gates
from .gates import judge_result, verify_pick
from .hashed_copy import reveal_all
from .inventory import Inventory
from .llm import Endpoint, endpoint_from_env
from .locator_policy import RANK, Written, kind_of, page_language
from .frames import HopHeal, Scope
from .audit import empty_result, run_audit
from .marks import run_overlay
from .diagnose import DIAG_MAX_TOKENS, diagnose_input, interpret, run_diagnosis
from .pick import run_pick
from .schemas import (LIMITS, Advice, AuditRequest, Candidate, DiagnoseRequest, DiagnoseResponse, Diagnosis, Expect,
                      FollowupRequest, HealRequest, HealResponse, ModelUsage, OverlayRequest, ResultRequest, Usage)
from .screenshot import MAX_BYTES, MAX_PIXELS, MAX_SIDE
from .store import DURABLE_DAYS, Store
from .telemetry import Telemetry
from .vision import run_vision_pick, twins_of
from .transport import Transport
from .triage import Triage

log = logging.getLogger("voyageur")

MAX_OPEN = 500                  # conversations held in memory
MAX_HELD_IMAGE_BYTES = 64 * 1024 * 1024    # screenshots kept for open conversations that may still need vision
_FACT_REFUSALS = ("renamed_neighbour", "moved_into_frame")
WHY = {
    "budget_exhausted": "the run's token budget is spent",
    "target_missing": "no element on the page could be the one the test meant",
    "no_pick": "the model gave no usable pick",
    "max_calls": "the model-call limit for one heal was reached",
    "model_not_configured": "no text model is configured for the pick",
    "no_stable_locator": "the element has no stable locator that is unique on the page (only structural or positional ones)",
    "language_branch_unavailable": "the page language differs from the passing run and the old copy is not known",
    "max_rounds": "the round limit was reached without a verified locator",
    "later_locator": "the healed line passed; the test now stops at a later locator that found nothing, which the next "
                     "round can heal",
    "no_candidate_left": "every locator written for the element failed in the live page",
    "pick_rejected": "every pick failed a gate",
    "no_identity_proof": "the model's pick could not be shown to be the element the test meant (no stable attribute, copy "
                         "or place of it matches what the step or the passing run recorded)",
    "deadline_exceeded": "reading this request took longer than the per-request time limit, so it was stopped",
    "worker_failed": "the worker that reads the request failed (out of memory or a crash), so it was stopped",
    "no_answer": "the model gave no answer",
    "located": "the failing locator is in the sent source: this is locator drift, use POST /v1/heal",
}
DIAGNOSE_OFF = "diagnose is off on this service"
_CACHEABLE = {"target_missing", "no_stable_locator", "language_branch_unavailable"}
_KIND_NAME = {"testid": "test_id"}


class UnknownHeal(KeyError):
    """No heal with that id (never started, already purged, or this process restarted mid-conversation)."""


class HealClosed(RuntimeError):
    """The heal already has its final answer; a result cannot reopen it."""


OVERLAY_WHY = {
    "viewport_mismatch": "the screenshot's shape does not match the viewport it was taken in",
    "no_viewport": "the request has no viewport size or device pixel ratio to place the boxes with",
    "undecodable": "the screenshot could not be decoded",
}


class OverlayRefused(ValueError):
    """POST /v1/overlay: the picture cannot be marked. `reason` is a code (a key of OVERLAY_WHY, or overlay_error); the message
    is a plain sentence that names the rule and never holds image data."""

    def __init__(self, reason: str):
        super().__init__(OVERLAY_WHY.get(reason, "the screenshot could not be marked"))
        self.reason = reason


class OverlayStopped(RuntimeError):
    """The worker for an overlay was stopped (`deadline_exceeded`) or died (`worker_failed`)."""

    def __init__(self, reason: str):
        super().__init__(WHY.get(reason, reason))
        self.reason = reason


class DiagnoseOff(RuntimeError):
    """POST /v1/diagnose was called while HEAL_DIAGNOSE is off: nothing was read, recorded or sent to a model."""


class Busy(RuntimeError):
    """Every audit slot is taken (a heal must always be able to get a worker); the client may retry shortly."""


class RepoMismatch(RuntimeError):
    """A follow-up names a repo other than the one the heal record holds."""


class NotHealed(RuntimeError):
    """A follow-up was sent for a heal that did not end `healed` (nothing was applied, so there is no PR to report on)."""


def _q(s: str) -> str:
    return norm(s).replace('"', "'")


def is_local(base_url: Optional[str]) -> bool:
    """True for an endpoint on this machine or a private network (its host is localhost or a private address)."""
    try:
        host = (urlsplit(base_url or "").hostname or "").lower()
    except ValueError:
        return False
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


@dataclass
class _Kept:
    """What a result round needs of the request, and nothing else: three options and the text of the files other
    patches may touch. The request itself (every source file, the failure text, the whole inventory) is dropped as soon
    as the first answer is built, so an open conversation is small."""
    test_id_attribute: str
    locale_branching: bool
    max_rounds: int
    files: Dict[str, Tuple[str, str]]               # path -> (sha256, content): only files a repeated call lives in

    @classmethod
    def of(cls, req: HealRequest, tri: Triage) -> "_Kept":
        used = {u.path for u in tri.uses}
        return cls(req.test_id_attribute, req.options.locale_branching, req.options.max_rounds,
                   {f.path: (f.digest, f.content) for f in req.source.files if f.path in used})


@dataclass
class _Open:
    """One heal in progress."""
    heal_id: str
    kept: _Kept
    tri: Triage
    inv: Inventory
    started: float
    run: str
    key: str
    page_lang: str
    last_lang: str
    fingerprint: Optional[Dict[str, Any]]
    broken_kind: str
    content: str
    target: Optional[Dict[str, Any]] = None
    how: str = ""
    settled_at: Optional[int] = None
    excluded: Set[str] = field(default_factory=set)
    shown: Set[str] = field(default_factory=set)
    queue: List[Written] = field(default_factory=list)
    below_floor: bool = False
    current: Optional[Written] = None
    current_to: str = ""
    response: Optional[HealResponse] = None
    round: int = 0
    tried: int = 0
    wrong: int = 0
    calls: int = 0
    usage: Dict[tuple, Dict[str, int]] = field(default_factory=dict)
    cache_hit: bool = False
    origin: Optional[str] = None
    budget_hit: bool = False
    pick_why: str = ""
    lock: Any = field(default_factory=threading.Lock)      # one result at a time per heal
    neighbours: List[Dict[str, Any]] = field(default_factory=list)
    dl: Optional[Budget] = None                            # the time this request may still spend in workers
    touched: float = 0.0                                   # last time the client spoke (self.clock), for the TTL
    scope: Optional[Scope] = None                          # the frame the step is in, when its call enters one
    stack: List[Any] = field(default_factory=list)          # the original failure's frames, innermost first
    hop: Optional[HopHeal] = None                          # the frame hop that was healed (frame_heal.py), if the call's was
    refusal: str = ""                                      # the code of the last gate that refused a pick
    advise: bool = False                                   # options.mode: answer with advice, no patch and no rounds
    evidence: Dict[str, Any] = field(default_factory=dict)  # the record's evidence (counts, hash); vision adds its own
    vision_mode: str = "off"                               # options.vision
    shot: Optional[bytes] = None                           # the client's PNG, held only while a vision call may need it
    shot_held: bool = False                                # counted in HealSession._held_images
    shot_size: int = 0
    viewport: Tuple[Optional[float], Optional[float], Optional[float]] = (None, None, None)   # CSS px w, h and dpr
    scroll: Tuple[float, float] = (0.0, 0.0)
    vision_done: bool = False                              # the one vision call has been used or ruled out
    escalate: bool = False                                 # the client's live check said wrong element: vision is next
    vision: Dict[str, Any] = field(default_factory=dict)   # what the record says about vision for this heal
    vision_why: str = ""                                   # the vision model's own words for the pick (untrusted)

    def pool(self) -> List[Dict[str, Any]]:
        """The elements a pick may come from: the frame's for an in-frame step, else the page's."""
        return self.scope.elements if self.scope else self.inv.elements


class HealSession:
    """The service core. One per process; safe to call from several threads."""

    def __init__(self, settings: ServiceSettings, store: Store, model: Any = None, endpoint: Optional[Endpoint] = None,
                 clock=time.monotonic, runner: Any = None, vision_endpoint: Optional[Endpoint] = None):
        self.settings, self.store, self.model, self.clock = settings, store, model, clock
        self.vision_endpoint = vision_endpoint or Endpoint()      # the vision role: unset means vision is unavailable
        self._held_images = 0                                     # bytes of screenshots held by open conversations
        self.runner = runner if runner is not None else InlineRunner()      # tests; from_env gives a process pool
        self.endpoint = endpoint or Endpoint()
        self._open: "OrderedDict[str, _Open]" = OrderedDict()
        self._spent: Dict[str, int] = {}
        self._lock = threading.RLock()
        self.telemetry = Telemetry()
        # at most workers-1 audits at once, so a heal can always get a worker (a single worker cannot reserve one)
        self._audit_slots = threading.BoundedSemaphore(max(1, settings.workers - 1))
        self._followup_lock = threading.Lock()               # a follow-up's read and write are one step

    @classmethod
    def from_env(cls, env: Mapping[str, str], db_path: Optional[str] = None) -> "HealSession":
        """A session configured from the environment: settings, store, the text endpoint and a real transport."""
        settings = ServiceSettings.from_env(env)
        if settings.retention_days <= DURABLE_DAYS:
            log.warning("HEAL_RETENTION_DAYS is %d: heals are purged before durability (still passing %d days later) can be "
                        "measured, so the dashboard shows no durability figure and a follow-up for an older heal is a 404. "
                        "Set it to at least %d to measure durability.", settings.retention_days, DURABLE_DAYS, DURABLE_DAYS + 1)
        if settings.overlay_retention_days > settings.retention_days > 0:
            log.info("HEAL_OVERLAY_RETENTION_DAYS (%d) is longer than HEAL_RETENTION_DAYS (%d): an image goes with its record.",
                     settings.overlay_retention_days, settings.retention_days)
        store = Store(db_path or settings.db_path, settings.retention_days, overlay_retention_days=settings.overlay_retention_days,
                      max_db_mb=settings.max_db_mb)
        runner: Any = None
        if settings.workers > 0:
            runner = ProcessRunner(settings.workers)
            runner.warm()
        return cls(settings, store, Transport(), endpoint_from_env("text", env), runner=runner,
                   vision_endpoint=endpoint_from_env("vision", env))

    def _budget(self) -> Budget:
        return Budget(self.runner, self.settings.request_deadline_s)

    # ---- public --------------------------------------------------------------------------------------------------
    def capabilities(self) -> Dict[str, Any]:
        ep, vp = self.endpoint, self.vision_endpoint
        return {"version": __version__,
                "models": {"text": {"configured": ep.configured, "provider": ep.provider, "model": ep.model,
                                    "local": is_local(ep.base) if ep.configured else None},
                           "vision": {"configured": vp.configured, "provider": vp.provider if vp.configured else None,
                                      "model": vp.model, "local": is_local(vp.base) if vp.configured else None,
                                      "images_allowed": bool(vp.configured and vp.images_allowed)}},
                "vision": {"available": bool(self.model is not None and vp.configured and vp.images_allowed)},
                "limits": {**LIMITS, "screenshot_bytes": MAX_BYTES, "screenshot_side_px": MAX_SIDE,
                           "screenshot_pixels": MAX_PIXELS, "candidate_cap": self.settings.candidate_cap, "max_calls": self.settings.max_calls,
                           "max_rounds": 5, "run_token_budget": self.settings.run_token_budget},
                "retention_days": self.settings.retention_days,
                "overlay_retention_days": self.settings.overlay_retention_days, "max_db_mb": self.settings.max_db_mb,
                "languages": ["python", "typescript"],
                "diagnose": {"enabled": self.settings.diagnose_enabled},
                "durability": {"available": self.settings.retention_days > DURABLE_DAYS,
                               "needs_retention_days": DURABLE_DAYS + 1},
                "library_classes": list(self.settings.library_classes)}

    def open_conversations(self) -> int:
        with self._lock:
            return len(self._open)

    def get(self, heal_id: str) -> Optional[Dict[str, Any]]:
        """The audit record of a heal (facts, model calls, candidates tried, outcome), or None."""
        return self.store.get_heal(heal_id)

    def start(self, req: HealRequest) -> HealResponse:
        """Run the ladder for one failure and answer with a candidate to try or a decision."""
        self.store.purge()
        self._expire()
        return self._start(req)

    def diagnose(self, req: DiagnoseRequest) -> DiagnoseResponse:
        """Name the likely cause of a failure that is not locator drift: one text call, checked, never cached. DiagnoseOff when
        the setting is off (no record, no call)."""
        if not self.settings.diagnose_enabled:
            raise DiagnoseOff(DIAGNOSE_OFF)
        self.store.purge()
        self._expire()
        return self._diagnose(req)

    def result(self, heal_id: str, res: ResultRequest) -> HealResponse:
        """Take the client's report on the candidate it tried and answer with the next candidate or the decision."""
        self._expire()
        with self._lock:
            o = self._open.get(heal_id)
        if o is None:
            if self.store.get_heal(heal_id) is not None:
                raise HealClosed(heal_id)
            raise UnknownHeal(heal_id)
        with o.lock:
            if heal_id not in self._open:                  # a concurrent result finished it first
                raise HealClosed(heal_id)
            o.touched = self.clock()
            o.dl = self._budget()                          # each request has its own time; the model call is not in it
            try:
                return self._result(o, res)
            except StageFailed as exc:
                return self._finish(o, "needs_human", exc.reason, WHY[exc.reason], 7)

    def audit(self, req: AuditRequest) -> Dict[str, Any]:
        """Rank the locators of the sent files against the page inventory (no model, no record), under its own work budget:
        past HEAL_AUDIT_BUDGET_S the answer is the rows done with `truncated` true, and a worker that had to be stopped gives
        no rows and `truncated` true, never an error. Busy when every audit slot is taken."""
        if not self._audit_slots.acquire(blocking=False):
            raise Busy("too many audits are running")
        try:
            budget = self.settings.audit_budget_s
            try:
                out = Budget(self.runner, budget + 3).run(run_audit, req, budget)
            except StageFailed:
                out = empty_result(req)
        finally:
            self._audit_slots.release()
        self.telemetry.inc("audits_total")
        return out

    def overlay(self, req: OverlayRequest) -> Dict[str, Any]:
        """Mark locators on the sent screenshot (marks.py): no model, no heal, no record, nothing kept. It takes one of the same
        slots as an audit (a heal must always be able to get a worker) and runs in a worker under the request deadline, with
        the audit's work budget for the matching. Busy when every slot is taken; OverlayRefused when the picture cannot be
        marked (it does not fit its viewport, or cannot be decoded); StageFailed's reason as OverlayStopped when the worker
        had to be stopped."""
        if not self._audit_slots.acquire(blocking=False):
            raise Busy("too many audits and overlays are running")
        try:
            try:
                out = self._budget().run(run_overlay, req, self.settings.audit_budget_s)
            except StageFailed as exc:
                raise OverlayStopped(exc.reason) from None
        finally:
            self._audit_slots.release()
        if "refused" in out:
            raise OverlayRefused(out["refused"])
        self.telemetry.inc("overlays_total")
        return out

    def followup(self, heal_id: str, req: FollowupRequest) -> Dict[str, Any]:
        """Store what a client saw after the heal's PR on the heal's record. The body must name the record's repo (a record
        with none takes only an empty repo) and, when it gives a run id, that record's run. `failed_again` stays true once it
        is, and the telemetry counts only a change of state, so sending the same report again changes nothing.
        UnknownHeal / NotHealed / RepoMismatch say why a report is refused. Reports run one at a time, so two identical
        reports sent at once cannot both read the old states and both count as forward."""
        with self._followup_lock:
            return self._followup(heal_id, req)

    def _followup(self, heal_id: str, req: FollowupRequest) -> Dict[str, Any]:
        rec = self.store.get_heal(heal_id)
        if rec is None:
            raise UnknownHeal(heal_id)
        if rec["outcome"] != "healed":
            raise NotHealed(heal_id)
        if req.repo != (rec.get("repo") or "") or (req.run_id is not None and req.run_id != rec.get("run_id")):
            raise RepoMismatch(heal_id)
        failed = bool(rec.get("failed_again")) or req.failed_again
        broke = rec.get("broke_again_after") if rec.get("failed_again") else None
        self.store.followup(heal_id, req.pr_state, failed, req.runs_since, req.days_since, broke)
        # counted once per (heal, state), and only going forward (none, then open, then a closed state): a PR that flips
        # between states cannot inflate the counters
        seen = set(filter(None, (rec.get("pr_states_seen") or "").split(",")))
        rank = {None: 0, "open": 1}
        forward = req.pr_state not in seen and rank.get(req.pr_state, 2) >= rank.get(rec.get("pr_state"), 2 if rec.get("pr_state") else 0)
        newly_failed = failed and not rec.get("failed_again")
        if forward or newly_failed:
            self.telemetry.followup(req.pr_state if forward else None, newly_failed)
        if forward:
            self.store.update_heal(heal_id, pr_states_seen=",".join(sorted(seen | {req.pr_state})))
        return {"heal_id": heal_id, "pr_state": req.pr_state, "failed_again": failed,
                "runs_since": req.runs_since, "days_since": req.days_since}

    # ---- the ladder ----------------------------------------------------------------------------------------------
    def _start(self, req: HealRequest) -> HealResponse:
        heal_id = "h_" + uuid.uuid4().hex[:24]
        started = self.clock()
        dl = self._budget()
        lang = req.client.language
        run = req.run.id if req.run and req.run.id else "default"
        stack = [(f.file, f.line) for f in req.failure.stack]
        page_lang = page_language(req.page.language.state())
        fp_model = req.last_pass.fingerprint if req.last_pass else None
        failed: Optional[StageFailed] = None
        try:
            an = dl.run(stages.analyze, req)                # steps 1 and 2 read client text: under the deadline
        except StageFailed as exc:
            an, failed = stages.Analysis(Triage("needs_human"), req.page.flat_inventory(), "", None), exc
        tri, flat, inv_hash = an.tri, an.flat, an.inv_hash
        inv = Inventory(flat, req.page.inventory_more == 0, req.page.aria_snapshot or "", req.page.inventory_copy_more,
                        req.page.inventory_copy_withheld, req.page.inventory_copy_withheld_data)
        evidence = {"files": len(req.source.files), "inventory": len(flat), "aria_chars": len(req.page.aria_snapshot or ""),
                    "inventory_hash": inv_hash, "stack": len(stack)}
        self.store.create_heal(
            heal_id, run_id=run, repo=req.run.repo if req.run else None, framework=req.client.framework, language=lang,
            test_id=req.test.id, page_path=req.page.path, page_language=page_lang or None, rounds=0, candidates_tried=0,
            wrong_element=0, covers=1, cache_hit=0, budget_exhausted=0, drift=0 if tri.status == "not_drift" else 1,
            outcome="try", mode=req.options.mode, evidence=evidence)
        fp, neighbours = reveal_all(fp_model.flat(), [n.flat() for n in req.last_pass.neighbours], flat) \
            if req.last_pass else (None, [])
        o = _Open(heal_id, _Kept.of(req, tri), tri, inv, started, run, "", page_lang, page_language({"lang": req.last_pass.language})
                  if req.last_pass else "", fp, "chain", "")
        o.neighbours = neighbours
        o.stack = list(req.failure.stack)                  # where the test first failed: a later failure must come after it
        o.dl = dl
        o.advise = req.options.mode == "advise"
        o.evidence = evidence
        self._set_vision(o, req)
        if failed is not None:
            return self._finish(o, "needs_human", failed.reason, WHY[failed.reason], 1)
        if tri.status != "located":
            return self._finish(o, tri.status, tri.reason, tri.why, 1)
        try:
            return self._ladder(o, req, an)
        except StageFailed as exc:
            return self._finish(o, "needs_human", exc.reason, WHY[exc.reason], 3)

    # ---- diagnose ------------------------------------------------------------------------------------------------------
    def _diagnose(self, req: DiagnoseRequest) -> DiagnoseResponse:
        heal_id = "h_" + uuid.uuid4().hex[:24]
        started = self.clock()
        dl = self._budget()
        run = req.run.id if req.run and req.run.id else "default"
        page_lang = page_language(req.page.language.state())
        failed: Optional[StageFailed] = None
        try:
            an = dl.run(stages.analyze, req)
        except StageFailed as exc:
            an, failed = stages.Analysis(Triage("needs_human"), req.page.flat_inventory(), "", None), exc
        tri = an.tri
        evidence: Dict[str, Any] = {"files": len(req.source.files), "inventory": len(an.flat), "stack": len(req.failure.stack),
                                    "aria_chars": len(req.page.aria_snapshot or ""), "prior_heals": len(req.prior_heals)}
        if req.options.vision != "off":
            evidence["vision"] = {"mode": req.options.vision, "ran": False, "skipped": "diagnose_text_only"}
        self.store.create_heal(
            heal_id, run_id=run, repo=req.run.repo if req.run else None, framework=req.client.framework,
            language=req.client.language, test_id=req.test.id, page_path=req.page.path, page_language=page_lang or None,
            rounds=0, candidates_tried=0, wrong_element=0, covers=1, cache_hit=0, budget_exhausted=0, drift=0, outcome="try",
            mode="diagnose", evidence=evidence)
        o = _Open(heal_id, _Kept.of(req, tri), tri, Inventory([], True), started, run, "", page_lang, "", None, "chain", "")
        o.dl, o.evidence = dl, evidence
        if failed is not None:
            return self._finish_diagnosis(o, "needs_human", failed.reason, WHY[failed.reason])
        if tri.status == "located":
            return self._finish_diagnosis(o, "not_applicable", "located", WHY["located"])
        budget = self.settings.run_token_budget
        if budget is not None and self._spent_by(run) >= budget:
            o.budget_hit = True
            return self._finish_diagnosis(o, "needs_human", "budget_exhausted", WHY["budget_exhausted"])
        if self.model is None or not self.endpoint.configured:
            return self._finish_diagnosis(o, "needs_human", "model_not_configured", WHY["model_not_configured"])
        try:
            di = dl.run(diagnose_input, req.source.files, [(f.file, f.line) for f in req.failure.stack], req.failure.error,
                        req.page.path, page_lang, req.page.aria_snapshot or "", an.flat, req.prior_heals, self.settings.candidate_cap)
        except StageFailed as exc:
            return self._finish_diagnosis(o, "needs_human", exc.reason, WHY[exc.reason])
        call = run_diagnosis(self.model, replace(self.endpoint, max_tokens=DIAG_MAX_TOKENS), di.prompt, heal_id)
        self._record_call(o, call)
        if call.text is None:
            return self._finish_diagnosis(o, "needs_human", "no_answer", WHY["no_answer"])
        try:
            diagnosis = dl.run(interpret, call.text, {h.heal_id for h in req.prior_heals}, di.standing,
                               {f.path: (f.digest, f.content) for f in req.source.files}, di.path, di.line)
        except StageFailed as exc:
            return self._finish_diagnosis(o, "needs_human", exc.reason, WHY[exc.reason])
        return self._finish_diagnosis(o, "diagnosed", diagnosis.cause, "", diagnosis)

    def _finish_diagnosis(self, o: _Open, status: str, reason: str, why: str, diagnosis: Optional[Diagnosis] = None) -> DiagnoseResponse:
        """The final answer of a diagnosis and its record: the cause, confidence and what was changed, never the model's words,
        the evidence or the edit (the store keeps no model text)."""
        usage = self._usage(o)
        seconds = self.clock() - o.started
        self.telemetry.heal(status, "diagnose", reason, seconds, False, 0)
        fields: Dict[str, Any] = {}
        if diagnosis is not None:
            self.telemetry.inc("diagnoses_total", cause=diagnosis.cause, confidence=diagnosis.confidence)
            fields = dict(confidence=diagnosis.confidence, patch_suggested=int(diagnosis.suggested_patch is not None),
                          downgraded=diagnosis.downgraded)
            o.evidence["diagnosis"] = {"cause": diagnosis.cause, "evidence": len(diagnosis.evidence_untrusted),
                                       "revert": diagnosis.revert_heal_id is not None, "downgraded": diagnosis.downgraded}
        self.store.update_heal(o.heal_id, outcome=status, reason=reason or None, why=None if diagnosis else why[:600] or None,
                               budget_exhausted=int(o.budget_hit), duration_ms=round(seconds * 1000), finished_at=time.time(),
                               evidence=o.evidence, **fields)
        self.store.finish(o.heal_id)
        log.info("diagnose %s outcome=%s reason=%s calls=%d tokens=%d", o.heal_id, status, reason or "-", usage.calls,
                 usage.input_tokens + usage.output_tokens)
        return DiagnoseResponse(heal_id=o.heal_id, status=status, diagnosis=diagnosis, reason=None if diagnosis else reason or None,
                                why=None if diagnosis else why or None, usage=usage)

    def _ladder(self, o: _Open, req: HealRequest, an: "stages.Analysis") -> HealResponse:
        tri, heal_id = o.tri, o.heal_id
        content = next(f.content for f in req.source.files if f.path == tri.path)
        o.content = content
        o.scope, o.hop = an.scope, an.hop
        o.broken_kind = kind_of(tri.call)
        locator_key = tri.path + "|" + _q(printed_of(tri.call))
        # the recorded fingerprint is in the key: a refusal cached without one must not stand once one is recorded
        recorded = req.last_pass.model_dump_json() if req.last_pass else ""
        # a decision reached with vision in play is not the one a text-only run would reach (a refusal cached without it
        # must not stand once an image can be looked at), so vision's availability is in the key; off adds nothing
        # what the probe left out or withheld, and whether the list is cut, are facts of the page too: they decide what the counts say
        gaps = "/".join(str(x) for x in (req.page.inventory_more, req.page.inventory_copy_more, req.page.inventory_copy_withheld,
                                         req.page.inventory_copy_withheld_data))
        # so is the aria snapshot: the text counts read it
        gaps += "/" + hashlib.sha256((req.page.aria_snapshot or "").encode()).hexdigest()
        parts = (_q(tri.call), an.inv_hash, o.page_lang, tri.language, req.test_id_attribute, recorded, gaps)
        o.key = hashlib.sha256("|".join(parts + (("vision",) if o.shot is not None else ())).encode()).hexdigest()
        self.store.update_heal(heal_id, file=tri.path, line=tri.line, broken_locator=tri.call[:500],
                               broken_kind=o.broken_kind, broken_tier=RANK[o.broken_kind], locator_key=locator_key[:600])
        by_ref = {e["ref"]: e for e in o.pool()}
        hit = self.store.cache_get(o.key)
        if hit and hit.get("kind") == "needs_human":
            o.cache_hit = True
            return self._finish(o, "needs_human", hit["reason"], hit.get("why") or WHY.get(hit["reason"], hit["reason"]),
                                hit["ended_at"])
        if hit and hit.get("kind") == "healed" and hit.get("ref") in by_ref:
            o.target, o.settled_at, o.cache_hit, o.origin = by_ref[hit["ref"]], hit["settled_at"], True, hit["origin"]
            o.how = "Settled earlier in this run (decision cache); verified again below."
        if o.target is None and an.matched and an.matched[0] in by_ref:
            o.target, o.settled_at = by_ref[an.matched[0]], 2
            o.how = "Same element as the passing run (" + ", ".join(an.matched[1]) + ")."
        if o.target is None:
            ended = self._pick(o)
            if ended is not None:
                return ended
        return self._offer(o)

    def _spent_by(self, run: str) -> int:
        with self._lock:
            mem = self._spent.get(run, 0)
        return max(mem, self.store.run_tokens(run))

    def _pick(self, o: _Open) -> Optional[HealResponse]:
        """Steps 3 and 4: one text call (more only when a gate or the client rules a pick out) and, when the vision rules in
        the module header ask for it, one vision call. Sets o.target, or returns the final answer when there is none."""
        budget = self.settings.run_token_budget
        if o.escalate:                                  # the client's live check found the last pick was the wrong element
            o.escalate = False
            el, final = self._vision_pass(o, "wrong_element")
            if final is not None:
                return final
            if el is not None:
                return self._take_vision(o, el)
        while True:
            if budget is not None and self._spent_by(o.run) >= budget:
                o.budget_hit = True
                return self._finish(o, "needs_human", "budget_exhausted", WHY["budget_exhausted"], 3)
            if o.calls >= self.settings.max_calls:
                return self._finish(o, "needs_human", "max_calls", WHY["max_calls"], 3)
            if self.model is None or not self.endpoint.configured:
                return self._finish(o, "needs_human", "model_not_configured", WHY["model_not_configured"], 3)
            refs, prompt = o.dl.run(stages.pick_input, o.content, o.tri.line, o.page_lang, o.pool(), o.tri.action,
                                    o.tri.call, self.settings.candidate_cap, o.excluded, o.fingerprint)
            if not refs and o.refusal == "no_identity_proof":
                return self._finish(o, "needs_human", "no_identity_proof", WHY["no_identity_proof"], 3)
            if not refs:
                why = WHY["target_missing"]
                if o.scope and o.scope.frame is None:
                    why += f" ({o.scope.why or 'the frame the step enters is not on the page: heal the frame locator first'})"
                return self._finish(o, "needs_human", "target_missing", why, 3, cache=("needs_human", "target_missing", 3))
            by_ref = {e["ref"]: e for e in o.pool()}
            shown = [by_ref[r] for r in refs]
            o.shown = set(refs)
            out = run_pick(self.model, self.endpoint, prompt, o.shown, o.heal_id)
            self._record_call(o, out.call)
            if out.pick.none and o.refusal == "no_identity_proof":     # the model's pick was refused, then nothing else fits:
                return self._finish(o, "needs_human", "no_identity_proof", WHY["no_identity_proof"], 3,   # the proof is what
                                    cache=("needs_human", "no_identity_proof", 3))                        # is missing
            if out.pick.none:
                if o.vision_mode == "on":                  # on looks at every heal, even one the text model gave up on
                    el, final = self._vision_pass(o, "text_none")
                    if final is not None:
                        return final
                    if el is not None:
                        return self._take_vision(o, el)
                return self._finish(o, "needs_human", "target_missing",
                                    WHY["target_missing"] + " (the model said none of the listed elements is the one)", 3,
                                    cache=("needs_human", "target_missing", 3))
            if out.pick.ref is None and o.refusal == "no_identity_proof":      # the earlier pick had no proof, this is no better
                return self._finish(o, "needs_human", "no_identity_proof", WHY["no_identity_proof"], 3)
            if out.pick.ref is None:
                el, final = self._vision_pass(o, "no_pick")
                if final is not None:
                    return final
                if el is not None:
                    return self._take_vision(o, el)
                return self._finish(o, "needs_human", "no_pick", WHY["no_pick"] + f" ({out.pick.reason})", 3)
            el = next(e for e in shown if e["ref"] == out.pick.ref)
            bad = self._gate(o, el)
            o.refusal = str(bad or "")
            if bad in _FACT_REFUSALS:                  # a fact about this page against the recording
                return self._finish(o, "needs_human", str(bad), bad.why[:600], 3, cache=("needs_human", str(bad), 3))
            if bad is None:
                o.target, o.settled_at, o.pick_why = el, 3, out.pick.why
                o.how = "The model picked this element from the list."
                trigger = "on" if o.vision_mode == "on" else "close_call" if o.vision_mode == "auto" \
                    and twins_of(el, shown) else ""
                if trigger:                            # a second look; a vision pick that passes the gates replaces this one
                    seen, _ = self._vision_pass(o, trigger, standing=True)
                    if seen is not None and seen["ref"] != el["ref"]:
                        return self._take_vision(o, seen, replaced=el["ref"])
                return None
            o.excluded.add(el["ref"])
            seen, final = self._vision_pass(o, "refused")
            if final is not None:
                return final
            if seen is not None:
                return self._take_vision(o, seen)
            if o.calls >= self.settings.max_calls:
                # every call the ladder may make was spent on picks the gates refused: the same inputs refuse the same way
                if bad == "no_identity_proof":
                    return self._finish(o, "needs_human", "no_identity_proof", WHY["no_identity_proof"], 3,
                                        cache=("needs_human", "no_identity_proof", 3))
                return self._finish(o, "needs_human", "pick_rejected", WHY["pick_rejected"] + f" ({bad})", 3,
                                    cache=("needs_human", "pick_rejected", 3))

    def _gate(self, o: _Open, el: Dict[str, Any]) -> Optional[Any]:
        """The gates a pick (text or vision) must pass: the refusal, or None."""
        return verify_pick(el, o.shown, o.pool(), o.fingerprint, o.tri.action, o.neighbours,
                           () if o.scope else o.inv.in_frames, o.tri.call,
                           bool(o.page_lang and o.last_lang and o.page_lang != o.last_lang),
                           o.hop.inner if o.hop else "", o.inv.complete and not o.inv.copy_more,
                           bool(o.inv.withheld_data))

    # ---- vision (step 4) -----------------------------------------------------------------------------------------
    def _set_vision(self, o: _Open, req: HealRequest) -> None:
        """Read the request's vision option. When vision cannot run for this heal, say why in the record and keep nothing."""
        o.vision_mode = req.options.vision
        if o.vision_mode == "off":
            return
        shot, vp = req.page.screenshot, self.vision_endpoint
        o.vision = {"mode": o.vision_mode, "ran": False}
        blocked = ("vision_not_configured" if self.model is None or not vp.configured
                   else "images_not_allowed" if not vp.images_allowed
                   else "no_screenshot" if shot is None else "")
        if blocked:
            o.vision.update(skipped=blocked)
            o.vision_done = True                   # nothing will be looked at: the image was checked at the door, but is never decoded, drawn, kept or sent
        else:
            view = req.page.viewport
            o.shot = shot.png()
            o.viewport = (view.w, view.h, view.dpr) if view else (None, None, None)
            o.scroll = (view.scroll.x, view.scroll.y) if view and view.scroll else (0.0, 0.0)
        self._save_vision(o)

    def _save_vision(self, o: _Open) -> None:
        if o.vision:
            self.store.update_heal(o.heal_id, evidence={**o.evidence, "vision": o.vision})

    def _release_image(self, o: _Open) -> None:
        o.shot = None
        if o.shot_held:
            o.shot_held = False
            with self._lock:
                self._held_images = max(0, self._held_images - o.shot_size)

    def _vision_pass(self, o: _Open, trigger: str, standing: bool = False) -> Tuple[Optional[Dict[str, Any]], Optional[HealResponse]]:
        """Step 4: (the element the vision model picked and the gates accepted, None); (None, None) when vision did not
        run or gave nothing usable (the record says why); (None, the final answer) when the gates say the page contradicts
        the recording (a fact, as for a text pick). `standing`: a text pick already passed the gates and stays unless this
        one replaces it, so a refusal here never ends the heal."""
        if o.vision_mode == "off" or o.vision_done or o.shot is None:
            return None, None
        v = o.vision
        v["trigger"] = trigger
        budget = self.settings.run_token_budget
        skip = ("budget_exhausted" if budget is not None and self._spent_by(o.run) >= budget
                else "max_calls" if o.calls >= self.settings.max_calls else "")
        if skip:
            v["skipped"] = skip                         # not final: the text outcome stands, and a later round may try again
            self._save_vision(o)
            return None, None
        o.vision_done = True                            # at most one vision call per heal
        png, o.shot = o.shot, None
        self._release_image(o)
        vi = o.dl.run(stages.vision_input, o.content, o.tri.line, o.page_lang, o.pool(), o.tri.action, o.tri.call,
                      self.settings.candidate_cap, o.excluded, o.fingerprint, png, o.viewport, o.scroll)
        if vi.skip or vi.overlay is None:
            v["skipped"] = vi.skip or "no_overlay"
            self._save_vision(o)
            return None, None
        by_ref = {e["ref"]: e for e in o.pool()}
        o.shown = set(vi.refs)
        out = run_vision_pick(self.model, self.vision_endpoint, vi.prompt, vi.overlay.png, len(vi.refs),
                              set(vi.overlay.drawn), o.heal_id)
        self._record_call(o, out.call, "vision", self.vision_endpoint, images=int(out.call.sent), step=4)
        v.update(ran=True, images=int(out.call.sent), marks=len(vi.overlay.drawn), not_visible=len(vi.overlay.not_visible),
                 image_px=[vi.overlay.width, vi.overlay.height])
        if out.call.sent:
            self.store.put_overlay(o.heal_id, vi.overlay.png)
        pick = out.pick
        if pick.mark is None:
            v["result"] = pick.reason or "no_pick"
            self._save_vision(o)
            return None, None
        el = by_ref[vi.refs[pick.mark - 1]]
        bad = self._gate(o, el)
        if bad is not None:
            v["result"] = f"refused:{bad}"
            o.excluded.add(el["ref"])
            self._save_vision(o)
            if bad in _FACT_REFUSALS and not standing:
                return None, self._finish(o, "needs_human", str(bad), bad.why[:600], 3, cache=("needs_human", str(bad), 3))
            return None, None
        v["result"] = "picked"
        o.vision_why = pick.why
        self._save_vision(o)
        return el, None

    def _take_vision(self, o: _Open, el: Dict[str, Any], replaced: Optional[str] = None) -> None:
        """The vision pick becomes the target (step 4 settled it); `replaced` is the text pick it overrode, if any."""
        o.target, o.settled_at, o.pick_why = el, 4, o.vision_why
        o.how = ("A vision model picked this element from the marked screenshot"
                 + (f" instead of {replaced}, the text model's pick." if replaced else "."))
        o.vision["changed"] = replaced is not None
        self._save_vision(o)
        return None

    def _record_call(self, o: _Open, call: Any, role: str = "text", endpoint: Optional[Endpoint] = None, images: int = 0,
                     step: int = 3) -> None:
        """Count one model call (text by default; vision passes its own endpoint, the images that left, and step 4)."""
        endpoint = endpoint or self.endpoint
        o.calls += 1
        usage = call.usage
        tokens = usage.input_tokens + usage.output_tokens
        with self._lock:
            self._spent[o.run] = self._spent.get(o.run, 0) + tokens
        model = endpoint.model or "unknown"
        row = o.usage.setdefault((role, model), {"calls": 0, "input_tokens": 0, "output_tokens": 0, "images": 0})
        row["calls"] += 1
        row["input_tokens"] += usage.input_tokens
        row["output_tokens"] += usage.output_tokens
        row["images"] += images
        self.store.add_call(o.heal_id, role=role, model=model, local=is_local(endpoint.base),
                            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, images=images,
                            latency_ms=call.duration_ms, ok=call.text is not None, step=step)
        self.telemetry.call(role, model, is_local(endpoint.base), call.text is not None, usage.input_tokens,
                            usage.output_tokens, images, call.duration_ms / 1000)

    def _offer(self, o: _Open) -> HealResponse:
        """Steps 5 and 6 for the settled target: write its locators and offer the first."""
        if o.hop and not o.how.startswith(o.hop.note):
            o.how = o.hop.note + " " + o.how
        res = o.dl.run(stages.write_for, o.inv, o.target["ref"], o.tri.language, o.tri.receiver, o.broken_kind,
                       o.kept.test_id_attribute, o.tri.call, o.scope, o.hop)
        if not res.candidates:
            return self._finish(o, "needs_human", "no_stable_locator", WHY["no_stable_locator"], 5,
                                cache=("needs_human", "no_stable_locator", 5))
        o.queue, o.below_floor = list(res.candidates), res.below_floor
        if o.advise:
            return self._advise(o)
        return self._next_try(o)

    def _advise(self, o: _Open) -> HealResponse:
        """Advise mode, steps 5 and 6: the best locator written for the settled element, offered as advice. Nothing is
        tried, no patch is built and no conversation stays open."""
        c, tri = o.queue[0], o.tri
        bp = o.dl.run(stages.plan_for, c, tri, o.last_lang, o.page_lang, o.fingerprint, o.kept.locale_branching)
        if bp.needed and bp.code is None:
            return self._finish(o, "needs_human", bp.reason, WHY[bp.reason], 6, cache=("needs_human", bp.reason, 6))
        to = bp.code or c.code[tri.language]
        kind, locator, movement, why = self._describe(o, c, bp)
        advice = Advice(target_ref=o.target["ref"], strategy=kind, locator=locator, code=to, branches=bp.branches,
                        why=why[:600], model_note_untrusted=o.pick_why[:200], tier=c.kind, tier_movement=movement,
                        below_floor=o.below_floor and movement == "down",
                        alternatives=[w.code[tri.language] for w in o.queue[1:6]])
        self.store.update_heal(o.heal_id, settled_at=o.settled_at, final_locator=to[:500], final_kind=c.kind,
                               final_tier=c.tier, tier_movement=movement, below_floor=int(advice.below_floor),
                               branch=int(bp.needed), cache_hit=int(o.cache_hit))
        return self._finish(o, "advised", "", "", 6 if bp.needed else 5, advice=advice)

    def _describe(self, o: _Open, c: Written, bp: Any) -> Tuple[str, Dict[str, Any], str, str]:
        """(strategy, language-neutral locator, tier movement, sentence) for locator `c` written for the settled element."""
        kind = _KIND_NAME.get(c.kind, c.kind)
        locator = bp.neutral if bp.needed else {"chain": c.neutral}
        locator = {"kind": "by_language" if bp.needed else kind, **locator}
        movement = "up" if c.tier < RANK[o.broken_kind] else "down" if c.tier > RANK[o.broken_kind] else "same"
        why = o.how + (f" A {kind} locator is unique inside the row {c.scope[:80]}, the only element holding that hook"
                       if c.scope else f" A {kind} locator is unique on the page")
        why += " and moves down a tier because nothing higher is unique." if (o.below_floor and movement == "down") \
            else f" ({movement} from the broken one)."
        return kind, locator, movement, why

    def _next_try(self, o: _Open) -> HealResponse:
        if not o.queue:
            return self._finish(o, "needs_human", "no_candidate_left", WHY["no_candidate_left"], 7)
        c = o.queue.pop(0)
        tri, kept = o.tri, o.kept
        bp, patch, also = o.dl.run(stages.patches_for, c, tri, o.last_lang, o.page_lang, o.fingerprint,
                                   kept.locale_branching, o.content, kept.files)
        if bp.needed and bp.code is None:
            return self._finish(o, "needs_human", bp.reason, WHY[bp.reason], 6, cache=("needs_human", bp.reason, 6))
        to = bp.code or c.code[tri.language]
        o.current, o.current_to = c, to
        o.round += 1
        o.tried += 1
        kind, locator, movement, why = self._describe(o, c, bp)
        cand = Candidate(target_ref=o.target["ref"], strategy=kind, locator=locator, code=to, branches=bp.branches,
                         expect=Expect(count=1, ref=o.target["ref"]), model_note_untrusted=o.pick_why[:200],
                         patch=patch, also_patches=also, why=why[:600],
                         tier=c.kind, tier_movement=movement, below_floor=o.below_floor and movement == "down")
        self.store.add_try(o.heal_id, o.round, o.target["ref"], c.kind, to[:500], "pending")
        self.store.update_heal(o.heal_id, outcome="try", rounds=o.round, candidates_tried=o.tried, settled_at=o.settled_at,
                               final_locator=to[:500], final_kind=c.kind, final_tier=c.tier, tier_movement=movement,
                               below_floor=int(cand.below_floor), branch=int(bp.needed), cache_hit=int(o.cache_hit))
        o.response = HealResponse(heal_id=o.heal_id, status="try", candidate=cand, round=o.round, usage=self._usage(o),
                                  cache_hit=o.cache_hit, settled_at=o.settled_at)
        self._remember(o)
        return o.response

    # ---- results -------------------------------------------------------------------------------------------------
    def _result(self, o: _Open, res: ResultRequest) -> HealResponse:
        ref = o.target["ref"]
        at = (o.tri.path, o.tri.line) if o.tri.path and o.tri.line else None
        verdict = judge_result(ref, res, o.current_to, at, self._own_check(o) if at else None, o.stack)
        hit = res.hit.ref if res.hit else None
        self.store.update_try(o.heal_id, o.round, verdict, f"count={res.count} hit={hit} outcome={res.test_outcome}")
        if verdict in ("ok", "ok_later"):
            # verify_passed says the whole test passed; a heal kept for a later failure records the reason instead
            self.store.update_heal(o.heal_id, verify_count=1, verify_ref=ref, verify_passed=int(verdict == "ok"))
            covers = 0 if o.origin else 1
            self.store.update_heal(o.heal_id, covers=covers)
            if o.origin:
                self.store.bump_covers(o.origin)
            else:
                self.store.cache_put(o.key, {"kind": "healed", "ref": ref, "settled_at": o.settled_at, "origin": o.heal_id})
            reason = "later_locator" if verdict == "ok_later" else ""
            return self._finish(o, "healed", reason, WHY[reason] if reason else "", 7,
                                candidate=o.response.candidate if o.response else None)
        if verdict == "wrong_element":
            o.wrong += 1
            o.excluded.add(ref)
            o.escalate = o.vision_mode != "off" and not o.vision_done      # the text pick failed live: look at the page
            self.store.update_heal(o.heal_id, wrong_element=o.wrong)
        if o.round >= o.kept.max_rounds:
            return self._finish(o, "needs_human", "max_rounds", WHY["max_rounds"], 7)
        if verdict == "wrong_element":
            o.target, o.settled_at, o.queue, o.cache_hit, o.origin = None, None, [], False, None
            o.refusal = ""
            ended = self._pick(o)
            return ended if ended is not None else self._offer(o)
        return self._next_try(o)

    @staticmethod
    def _own_check(o: _Open) -> Optional[int]:
        """The healed statement's own `expect(` line in the file as patched; None when an extra patch in the same file could
        have moved lines."""
        cand = o.response.candidate if o.response else None
        if cand is None or cand.patch is None or any(p.file == cand.patch.file for p in cand.also_patches or []):
            return None
        line = gates.own_check_line(o.content, o.content.count("\n", 0, o.tri.end) + 1)
        return line + cand.patch.to.count("\n") - cand.patch.from_.count("\n") if line else None

    # ---- bookkeeping ---------------------------------------------------------------------------------------------
    def _usage(self, o: _Open) -> Usage:
        rows = [ModelUsage(role=r, model=m, **v) for (r, m), v in sorted(o.usage.items())]
        return Usage(calls=sum(r.calls for r in rows), input_tokens=sum(r.input_tokens for r in rows),
                     output_tokens=sum(r.output_tokens for r in rows), images=sum(r.images for r in rows), by_model=rows)

    def _expire(self) -> None:
        """Close every open conversation the client has not spoken to for `open_ttl_s`: a client that crashed after
        `start` never sends a result, and its state must not stay in memory. The record says why it ended."""
        ttl, now, gone = self.settings.open_ttl_s, self.clock(), []
        with self._lock:
            while self._open:
                first = next(iter(self._open.values()))
                if now - first.touched < ttl or not first.lock.acquire(blocking=False):
                    break
                self._open.popitem(last=False)
                first.lock.release()
                self._release_image(first)
                gone.append(first)
        for o in gone:
            self.store.update_heal(o.heal_id, outcome="needs_human", reason="open_expired",
                                   why="the client sent no result within the open-conversation time limit",
                                   finished_at=time.time())
            self.store.finish(o.heal_id)
            self.telemetry.heal("needs_human", "heal", "open_expired", now - o.started, o.cache_hit, 0,
                                sorted({m for _, m in o.usage}))

    def _remember(self, o: _Open) -> None:
        o.touched = self.clock()
        with self._lock:
            if o.shot is not None and not o.shot_held:          # kept for a later round only while there is room for it
                if self._held_images + len(o.shot) <= MAX_HELD_IMAGE_BYTES:
                    o.shot_size, o.shot_held = len(o.shot), True
                    self._held_images += o.shot_size
                else:
                    o.shot, o.vision_done = None, True
                    o.vision["skipped"] = "image_not_kept"
                    self._save_vision(o)
            self._open[o.heal_id] = o
            self._open.move_to_end(o.heal_id)
            while len(self._open) > MAX_OPEN:
                self._release_image(self._open.popitem(last=False)[1])

    def _finish(self, o: _Open, status: str, reason: str, why: str, ended_at: int, candidate: Optional[Candidate] = None,
                cache: Optional[tuple] = None, advice: Optional[Advice] = None) -> HealResponse:
        usage = self._usage(o)
        seconds = self.clock() - o.started
        self.telemetry.heal(status, "advise" if o.advise else "heal", reason, seconds, o.cache_hit, o.wrong,
                            sorted({m for _, m in o.usage}))
        self.store.update_heal(o.heal_id, outcome=status, reason=reason or None, why=why[:600] or None,
                               ended_at=ended_at, settled_at=o.settled_at, rounds=o.round, candidates_tried=o.tried,
                               wrong_element=o.wrong, cache_hit=int(o.cache_hit), budget_exhausted=int(o.budget_hit),
                               duration_ms=round(seconds * 1000), finished_at=time.time())
        if cache:
            self.store.cache_put(o.key, {"kind": cache[0], "reason": cache[1], "ended_at": cache[2], "why": (why or "")[:600]})
        self._release_image(o)
        self._save_vision(o)
        self.store.finish(o.heal_id)
        with self._lock:
            self._open.pop(o.heal_id, None)
        log.info("heal %s outcome=%s reason=%s calls=%d tokens=%d", o.heal_id, status, reason or "-", usage.calls,
                 usage.input_tokens + usage.output_tokens)
        return HealResponse(heal_id=o.heal_id, status=status, candidate=candidate, advice=advice, round=o.round,
                            reason=reason or None, why=why or None, usage=usage, cache_hit=o.cache_hit, settled_at=o.settled_at,
                            ended_at=ended_at)


__all__ = ["HealSession", "UnknownHeal", "HealClosed", "NotHealed", "Busy", "RepoMismatch", "DiagnoseOff", "OverlayRefused",
           "OverlayStopped", "is_local"]
