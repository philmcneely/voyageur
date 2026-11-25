#!/usr/bin/env python3
"""
===============================================================================
Testbed Acceptance Run (the suites, the drifted sites and the service, end to end)
===============================================================================
Serves the testbed's static sites, runs each Python suite against every drifted variant and, for each failure, does what a
framework's heal client does: build a real POST /v1/heal request (the probe run by Playwright on the failing page, the test
file, the fingerprint recorded on the base site), send it, and walk the try -> result conversation. A `try` candidate is checked
in the live page (one match, and which element), applied to a temporary copy of the suite, and the exact test is re-run before
the result is reported; a candidate that does not hold is reverted and the next one is asked for. The repository's own test files
are never edited. Every variant ends with a row: the outcome, the step that settled the element, the model calls and tokens, and
the wrong-element catches; the rows go to JSON and markdown.

The model is `--model fake` (offline: the pick the expected file names is scripted, the usage is a fixed estimate of the prompt
size, no endpoint is called) or `--model env` (the service's own HEAL_TEXT_* variables name a real model; nothing is set or
guessed here). `--service-url` drives an already running service instead of an in-process one.

`--vision off|auto|on` is the heal client's HEAL_VISION. With `off` (the default) nothing about the request changes: no screenshot is
taken and none is sent. With `auto` or `on` the failing run also dumps one viewport screenshot, the request carries it with the
viewport, and the service may use it (see core.py). In `--model fake` the vision model is scripted too and is deliberately not an
oracle everywhere: when the golden element exists it answers with that element's mark, and when the element is gone (or not listed)
it answers with the first drawn mark, as a hallucinating model would. The gates must refuse that, so `wrong kept` must stay 0.

How a re-run is judged: as a real client does it. The bench reports `passed`, `failed`, or `failed_later` with the later
failure's error, file and line when the re-run stopped past the patched line, and the SERVICE judges (gates.judge_result): a
later locator that found nothing, or any failure past the healed statement's own `expect(` check, keeps the heal (the statement
and its check both held; the next drift is the next heal); anything else, or the same statement again, is a failed re-run and
the candidate is reverted. An assertion that fails on the healed statement's own check therefore rejects the heal, which is how
a locator that hit the wrong element is caught.

Features:
    ✓ Fully offline with --model fake; the same code path drives a real model
    ✓ --vision auto|on adds the screenshot and a scripted vision model, and the report adds vision calls, images, how often vision
      ran and how often it changed the text pick
    ✓ Expected outcomes come from golden-*.json (the correct heal of every broken locator, or null = the element is gone)
    ✓ Reports per variant: outcome counts, settled-at step histogram (2 same-element match, 3 text pick), model calls, input
      and output tokens, wrong-element catches, tests passed, and whether every step met its expectation
    ✓ Exit status 1 when any step misses its expectation, any healed step is not on the golden element, or a base run is red
    ✓ For testbed/bench.py (the model comparison): every row also holds the service's `duration_ms` and the golden element
      (`golden_target`), and an in-process run ends with `service_metrics` (the service's own cost, latency and outcomes by model
      for this run); --candidate-cap and --max-calls set the pipeline's knobs of the scripted model

Environment Variables:
    HEAL_TEXT_BASE_URL, HEAL_TEXT_MODEL, HEAL_TEXT_API_KEY, HEAL_TEXT_PROVIDER   (--model env) the model the service uses
    HEAL_VISION_BASE_URL, HEAL_VISION_MODEL, HEAL_VISION_API_KEY, HEAL_VISION_PROVIDER, HEAL_VISION_IMAGES_ALLOWED
                                                                                 (--model env --vision auto|on) the vision model
    HEAL_SERVICE_URL, HEAL_SERVICE_TOKEN                                         (--service-url) a running service
    QA_ACTION_TIMEOUT_MS                                                         set by this script for the suites

Author: PMAC
===============================================================================
"""
import argparse
import functools
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

import serve  # noqa: E402  (the testbed's own static server handler)
import ts_client  # noqa: E402  (--client ts: the TypeScript client's driver)

PY_DIR = HERE / "tests" / "python"
SPECS = {
    "drift": {"file": "test_drift.py", "base": "drift-base", "golden": "golden-drift.json"},
    "orders": {"file": "test_orders.py", "base": "orders-base", "golden": "golden-orders.json"},
    "real": {"file": "test_real.py", "base": "real-base", "golden": "golden-real.json"},
}
DEFAULT_SPECS = ["drift", "orders"]
MAX_STEPS_PER_TEST = 60


# ---- the golden files ------------------------------------------------------------------------------------------------
def load_golden(spec: str) -> Dict[str, List[Dict[str, Any]]]:
    """{variant: [step]} where a step is {old, target, expect, test?}. `target` is a Playwright (Python) expression for the
    element a correct heal ends on, None when the element is gone."""
    data = json.loads((HERE / SPECS[spec]["golden"]).read_text(encoding="utf-8"))
    variants = data["variants"] if "variants" in data else data
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name, heals in variants.items():
        steps = []
        for h in heals:
            old = h["old"]["python"] if isinstance(h["old"], dict) else h["old"]
            new = h["new"]["python"] if isinstance(h.get("new"), dict) else h.get("new")
            old_ts = h["old"].get("ts") if isinstance(h["old"], dict) else None
            new_ts = h["new"].get("ts") if isinstance(h.get("new"), dict) else None
            # an explicit "target": null beside a replacement means the replacement is the target (only a step with no
            # replacement has no target: the element is gone)
            target = (h["target"] or new) if "target" in h else new
            expect = h.get("expect") or {"outcome": "needs_human" if target is None else "healed"}
            steps.append({"old": old, "new": new, "target": target, "expect": expect, "test": h.get("test"),
                          "compose": "target" not in h,
                          # the same expressions as the TypeScript suites write them (--client ts)
                          "old_ts": old_ts or ts_client.py_to_ts(old), "new_ts": new_ts or (ts_client.py_to_ts(new) if new else None),
                          "target_ts": new_ts if (new_ts and target == new) else (ts_client.py_to_ts(target) if target else None)})
        out[name] = steps
    return out


def scrub_model_credentials(env: Any) -> List[str]:
    """Remove the model credentials (HEAL_*_API_KEY, _HEADERS, _EXTRA_BODY) from `env`, once the service has been built from them:
    the suites, the browser and the TypeScript client are started with a copy of the environment and have no use for a key."""
    gone = [k for k in list(env) if k.startswith("HEAL_") and k.endswith(("API_KEY", "_HEADERS", "_EXTRA_BODY"))]
    for k in gone:
        del env[k]
    return gone


GAPS_FILE = HERE / "acceptance-gaps.json"
GAP_NOTES = {
    "source_not_found": "the failing expression was not found in the sent source (printed form differs from the written one)",
    "max_rounds": "three locators were tried and none verified",
    "target_missing": "no element on the page could be the target (for a step in a frame: the frame the call enters is not on the page, its hop is the broken part)",
    "no_stable_locator": "the element has only structural, positional or copy-inside-a-container locators; none is written",
    "no_pick": "the golden element was not on the pick list (candidate cap, action read as none after .first/.nth, or a frame hop)",
    "no_identity_proof": "the pick (the golden element) shares no stable attribute, copy or place with what the broken locator says, so it is not proven to be the element the step meant",
    "assertion_mismatch": "an old locator that still matches another element fails on the next assertion; that is not drift, so nothing is healed",
    "no_candidate_left": "a text locator resolves to a child of the listed element, so the hit cannot be verified",
}


def load_gaps() -> Dict[str, List[Dict[str, Any]]]:
    return json.loads(GAPS_FILE.read_text(encoding="utf-8")) if GAPS_FILE.exists() else {}


# ---- the service, in process or over HTTP -----------------------------------------------------------------------------
IMAGE_TOKENS = 85       # the fixed estimate a scripted vision call adds for its one image


class ScriptedModel:
    """A stand-in model: it answers each pick with the ref the runner scripted from the expected file (None = no usable
    answer). Usage is a fixed estimate (four characters a token), so the figures are reproducible.

    A call that carries an image is the scripted VISION model: `target` is the candidate line of the golden element (set by the
    runner) and the answer is the mark whose line is that one. With no golden element, or one the list does not show, it answers
    with the first mark that is drawn, the way a model that must always name something does."""

    def __init__(self) -> None:
        self.script: List[Optional[str]] = []
        self.target: Optional[str] = None
        self.calls = 0
        self.vision_calls = 0
        self.images = 0

    def call(self, endpoint: Any, prompt: str, images: Any = (), heal_id: str = "") -> Any:
        from voyageur.llm import Usage
        from voyageur.transport import CallResult
        self.calls += 1
        if images:
            self.vision_calls += 1
            self.images += len(images)
            marks = [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^\[(\d+)\](.*)$", prompt, re.M)]
            drawn = [n for n, rest in marks if "(not visible in the image)" not in rest]
            wanted = [n for n, rest in marks if self.target is not None and rest.strip() == self.target and n in drawn]
            answer = (wanted or drawn or [None])[0]
            text = json.dumps({"mark": answer, "why": "scripted"})
            return CallResult(text, "", Usage(max(1, len(prompt) // 4) + IMAGE_TOKENS * len(images), max(1, len(text) // 4)),
                              1, True)
        ref = self.script.pop(0) if self.script else None
        text = json.dumps({"ref": ref, "why": "scripted from the expected file"}) if ref else "NONE"
        return CallResult(text, "", Usage(max(1, len(prompt) // 4), max(1, len(text) // 4)), 1, True)


SERVE_START_S = 30.0                         # how long --client ts waits for the in-process HTTP server to start


class Service:
    """The heal API as the runner uses it: post / get returning decoded JSON."""

    def __init__(self, mode: str, url: Optional[str], candidate_cap: Optional[int] = None, vision: str = "off",
                 max_calls: Optional[int] = None) -> None:
        self.model: Optional[ScriptedModel] = None
        headers: Dict[str, str] = {}
        self.http_url = None
        if url:
            import httpx
            token = os.environ.get("HEAL_SERVICE_TOKEN")
            self.client = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=300)
            self.label = "external service"
            return
        from fastapi.testclient import TestClient
        from voyageur.app import create_app
        from voyageur.config import ServiceSettings
        from voyageur.core import HealSession
        from voyageur.llm import Endpoint
        from voyageur.store import Store
        if mode == "fake":
            self.model = ScriptedModel()
            settings = ServiceSettings(retention_days=1, **({"candidate_cap": candidate_cap} if candidate_cap else {}),
                                       **({"max_calls": max_calls} if max_calls else {}))
            core = HealSession(settings, Store(":memory:", 1), self.model, Endpoint(base_url="http://localhost:1/v1",
                                                                                     model="scripted-fake"),
                               vision_endpoint=Endpoint(base_url="http://localhost:1/v1", model="scripted-fake-vision",
                                                        images_allowed=True))
            self.label = "in-process service, scripted fake model"
        else:
            core = HealSession.from_env(os.environ, db_path=":memory:")
            if not core.endpoint.configured:
                raise SystemExit("run_acceptance: --model env needs the service's model settings (HEAL_TEXT_BASE_URL and "
                                 "HEAL_TEXT_MODEL, or HEAL_BASE_URL and HEAL_MODEL) in the environment")
            if vision != "off" and not (core.vision_endpoint.configured and core.vision_endpoint.images_allowed):
                raise SystemExit("run_acceptance: --vision auto|on with --model env needs HEAL_VISION_BASE_URL, HEAL_VISION_MODEL "
                                 "and HEAL_VISION_IMAGES_ALLOWED=true in the environment")
            self.label = f"in-process service, model {core.endpoint.model}"
        if core.settings.token:
            headers["Authorization"] = f"Bearer {core.settings.token}"
        self.app = create_app(core)
        self.client = TestClient(self.app, base_url="http://127.0.0.1", headers={"Content-Type": "application/json", **headers})
        self.core = core
        self.http_url: Optional[str] = None

    def serve_http(self) -> str:
        """Serve the same app over HTTP on a free local port (a client in another process, --client ts, talks to it). A scripted
        model gets a /_testbed/script route: the client posts the golden element's ref there before it asks for a heal."""
        import uvicorn
        from voyageur.cli import KEEP_ALIVE_S
        if self.http_url is None:
            if self.model is not None:
                model = self.model

                @self.app.post("/_testbed/script")
                def script(body: Dict[str, Any]) -> Dict[str, Any]:
                    model.script = list(body.get("script") or [])
                    model.target = None
                    return {"ok": True}
            import socket
            # uvicorn serves on this socket: closing it and binding the port again would let a parallel run take it in between
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="warning",
                                                   timeout_keep_alive=KEEP_ALIVE_S))
            threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True).start()
            deadline = time.monotonic() + SERVE_START_S
            while not server.started:
                if time.monotonic() > deadline:
                    server.should_exit = True
                    sock.close()
                    raise RuntimeError(f"the in-process service did not start on port {port} within {SERVE_START_S:g} s")
                time.sleep(0.05)
            self.http_server, self.http_url = server, f"http://127.0.0.1:{port}"
        return self.http_url

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        r = self.client.post(path, json=body)
        if r.status_code != 200:
            raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def get(self, path: str) -> Any:
        r = self.client.get(path)
        if r.status_code != 200:
            raise RuntimeError(f"GET {path} -> {r.status_code}")
        return r.json() if "json" in r.headers.get("content-type", "") else r.text


# ---- running a suite -------------------------------------------------------------------------------------------------
class Workdir:
    """A temp copy of the suite for one (spec, variant): the test file, conftest.py and pytest.ini. Heals are applied to
    this copy and nowhere else."""

    def __init__(self, spec: str, base_url: str, probe_path: Path, timeout_ms: int, steps: Optional[List[Dict[str, Any]]] = None) -> None:
        self.spec, self.test_file = spec, SPECS[spec]["file"]
        self.dir = Path(tempfile.mkdtemp(prefix=f"heal-testbed-{spec}-"))
        for name in ("conftest.py", "pytest.ini", self.test_file):
            shutil.copy2(PY_DIR / name, self.dir / name)
        self.last: Optional[Dict[str, Any]] = None
        self.failures = self.dir / "failures"
        self.env = dict(os.environ, QA_BASE_URL=base_url, QA_ACTION_TIMEOUT_MS=str(timeout_ms), HEAL_PROBE_JS=str(probe_path),
                        HEAL_TESTBED_FAILURES=str(self.failures))
        self.env.pop("HEAL_TESTBED_RECORD", None)
        if steps is not None:
            targets = self.dir / "targets.json"
            targets.write_text(json.dumps([{"old": s["old"], "new": s["new"], "target": s["target"], "compose": s["compose"],
                                      "test": s.get("test")} for s in steps]),
                               encoding="utf-8")
            self.env["HEAL_TESTBED_TARGETS"] = str(targets)
        self.runs = 0

    @property
    def text(self) -> str:
        return (self.dir / self.test_file).read_text(encoding="utf-8")

    def write(self, text: str) -> None:
        (self.dir / self.test_file).write_text(text, encoding="utf-8")

    def run(self, test: Optional[str] = None, record: bool = False) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
        """Run one test (or the whole file). Returns ({test name: result} from the JUnit report, the per-statement record
        of the run when `record`: {"<file>:<line>": {count, element, lang}})."""
        self.runs += 1
        xml = self.dir / "report.xml"
        target = f"{self.test_file}::{test}" if test else self.test_file
        env = dict(self.env)
        recdir = self.dir / f"record-{self.runs}"
        if record:
            env["HEAL_TESTBED_RECORD"] = str(recdir)
        subprocess.run([sys.executable, "-m", "pytest", target, "-q", "--tb=short", "-p", "no:cacheprovider",
                        f"--junitxml={xml}"], cwd=self.dir, env=env, capture_output=True, text=True, timeout=900)
        out: Dict[str, Dict[str, Any]] = {}
        for tc in ET.parse(xml).getroot().iter("testcase"):
            name = tc.get("name") or ""
            bad = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
            if bad is None:
                out[name] = {"result": "passed"}
                continue
            frames = [(m.group(1), int(m.group(2))) for m in re.finditer(r"^(test_\w+\.py):(\d+): in ", bad.text or "", re.M)]
            out[name] = {"result": "failed", "error": (bad.get("message") or bad.text or "").strip()[:20000],
                         "frames": frames, "line": frames[0][1] if frames else None}
        statements: Dict[str, Any] = {}
        if record and (recdir / "statements.json").exists():
            statements = json.loads((recdir / "statements.json").read_text(encoding="utf-8"))
        return out, statements

    def failure(self, test: str) -> Optional[Dict[str, Any]]:
        """What the failing run dumped about the page at the failure (URL, probe listing, aria snapshot, golden target)."""
        path = self.failures / f"{test}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
        return data

    def line_text(self, line: Optional[int]) -> str:
        lines = self.text.split("\n")
        return lines[line - 1].strip() if line and 0 < line <= len(lines) else ""


# ---- the page: matching probe items ------------------------------------------------------------------------------------
LOOSE_SKIP = {"ref", "box", "frame", "shadow", "row"}


def same_item(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Whether two probe items describe the same element, comparing everything the probe lists."""
    keys = lambda d: {k: v for k, v in d.items() if k != "ref"}  # noqa: E731
    return keys(a) == keys(b)


def ref_in(items: List[Dict[str, Any]], item: Optional[Dict[str, Any]], frame: str = "") -> Optional[str]:
    """The ref (e<position>) of `item` in a probe listing. An item described on its own (probe `only` mode) can differ from the
    whole-page listing in where it sits (a box inside a frame is relative to the frame; a row or shadow host may be unset), so
    when no entry is identical the one entry equal on every other fact is taken, provided it is the only one. `frame` is the
    name of the frame the element sits in, which a lone description leaves out: it tells the element from its twins outside."""
    if not item:
        return None
    exact = [i for i, other in enumerate(items) if same_item(item, other)]
    if exact:
        return f"e{exact[0]}"
    loose = [i for i, other in enumerate(items)
             if {k: v for k, v in item.items() if k not in LOOSE_SKIP} == {k: v for k, v in other.items() if k not in LOOSE_SKIP}]
    if len(loose) > 1 and frame:
        loose = [i for i in loose if items[i].get("frame", "") == frame]
    return f"e{loose[0]}" if len(loose) == 1 else None


def fingerprint_of(item: Dict[str, Any]) -> Dict[str, Any]:
    """The request's fingerprint shape for a recorded probe item: text facts, an attrs bag and a box; never a value."""
    fp: Dict[str, Any] = {k: item[k] for k in ("tag", "type", "role", "name", "label", "placeholder", "text", "row", "frame")
                          if isinstance(item.get(k), str) and item[k]}
    attrs = {attr: item[key] for key, attr in (("id", "id"), ("testid", "data-testid"), ("name_attr", "name"),
                                               ("aria_label", "aria-label"), ("class", "class"), ("title", "title"),
                                               ("alt", "alt")) if isinstance(item.get(key), str) and item[key]}
    attrs.update({k.replace("_", "-"): v for k, v in item.items() if k.startswith("data_") and isinstance(v, str) and v})
    fp["attrs"] = dict(list(attrs.items())[:40])
    if item.get("box"):
        fp["box"] = item["box"]
    return fp


# ---- one heal --------------------------------------------------------------------------------------------------------
def apply_patches(original: str, patches: List[Dict[str, Any]]) -> str:
    """The file with every patch applied. Each patch is checked against the file it was built from (hash and `from` text)
    and applied from the end of the file so offsets stay valid."""
    digest = hashlib.sha256(original.encode("utf-8", "replace")).hexdigest()
    text = original
    for p in sorted(patches, key=lambda p: p["span"]["start"], reverse=True):
        if p["base_sha256"] != digest:
            raise RuntimeError("a patch is not based on the file as it is")
        start, end = p["span"]["start"], p["span"]["end"]
        if original[start:end] != p["from"]:
            raise RuntimeError("a patch's `from` text is not at its span")
        text = text[:start] + p["to"] + text[end:]
    return text


def rerun_outcome(result: Dict[str, Any], patched_line: int) -> str:
    """passed | failed_later | failed for the exact test after a patch, as a real client reports it: failed_later when the
    re-run stopped on a line past the patched one. The service judges whether that keeps the heal (see the module docstring)."""
    if result["result"] == "passed":
        return "passed"
    line = result.get("line")
    return "failed_later" if line is not None and line > patched_line and result.get("frames") else "failed"


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def target_line(items: List[Dict[str, Any]], ref: Optional[str]) -> Optional[str]:
    """What the scripted vision model looks for in the numbered list: the golden element's candidate line without its ref."""
    if not ref:
        return None
    from voyageur.pick import candidate_line
    from voyageur.schemas import InventoryItem
    index = int(ref[1:])
    line = candidate_line(InventoryItem.model_validate(items[index]).flat(index))
    return line[len(ref):].strip()


def screenshot_of(dump: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The request's `page.screenshot` for a dump that holds one: the PNG as base64 with the PNG's own width and height."""
    import base64
    import struct
    b64 = dump.get("screenshot_b64")
    if not b64:
        return None
    width, height = struct.unpack(">II", base64.b64decode(b64[:64])[16:24])
    return {"media_type": "image/png", "data": b64, "width": width, "height": height}


def heal_step(svc: Service, wd: Workdir, dump: Dict[str, Any], test: str, result: Dict[str, Any], step: Optional[Dict[str, Any]],
              fingerprints: Dict[str, Any], run_id: str, use_fingerprints: bool, vision: str = "off") -> Dict[str, Any]:
    """Heal one failing statement through the API. `dump` is the page as it was at the failure. Returns the row: the service's
    final answer and what happened on the way."""
    source = wd.text
    items, record = dump["probe"]["items"], dump["probe"]["page"]
    key = f"{wd.test_file}:{result['line']}"
    body: Dict[str, Any] = {
        "client": {"framework": "voyageur-testbed", "language": "python", "version": "1"},
        "test": {"id": f"{wd.test_file}::{test}", "title": test},
        "failure": {"error": result["error"], "stack": [{"file": f, "line": n} for f, n in result["frames"]]},
        "source": {"roots": [], "files": [{"path": wd.test_file, "sha256": hashlib.sha256(source.encode()).hexdigest(),
                                           "content": source}]},
        "page": {"url": dump["url"],
                 "language": {"html_lang": record.get("lang") or None, "browser_locale": record.get("browser_lang") or None},
                 "inventory": items, "inventory_more": dump["probe"].get("more", 0),
                 "inventory_copy_more": dump["probe"].get("copy_more", 0),
                 "inventory_copy_withheld": dump["probe"].get("copy_withheld", 0),
                 "inventory_copy_withheld_data": dump["probe"].get("copy_withheld_data", 0), "aria_snapshot": dump.get("aria")},
        "options": {"max_rounds": 3}, "run": {"id": run_id, "repo": "testbed"}}
    if vision != "off":                                       # HEAL_VISION: the client sends one viewport screenshot
        body["options"]["vision"] = vision
        body["page"]["screenshot"] = screenshot_of(dump)
        body["page"]["viewport"] = dump.get("viewport")
    if use_fingerprints and key in fingerprints and fingerprints[key].get("element"):
        body["last_pass"] = {"fingerprint": fingerprint_of(fingerprints[key]["element"]),
                             "language": fingerprints[key].get("lang") or None}
    target_ref = ref_in(items, dump.get("target_item"), dump.get("target_frame") or "")
    target_note = dump.get("target_error") if "target_error" in dump else "no golden step matched the failing line"
    if target_ref is None and dump.get("target_item"):
        target_note = "the golden element is not in the probe listing"
    if svc.model is not None:
        svc.model.script = [target_ref] * 3 if target_ref else [None]
        svc.model.target = target_line(items, target_ref)
    row: Dict[str, Any] = {"target_note": None if target_ref else target_note, "test": test, "line": result["line"], "statement": wd.line_text(result["line"])[:200],
                           "expected": (step or {}).get("expect", {"outcome": "unlisted"}), "target_ref": target_ref}
    if os.environ.get("HEAL_TESTBED_DUMP_REQUESTS"):          # debugging: keep every request as sent
        out = Path(os.environ["HEAL_TESTBED_DUMP_REQUESTS"])
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{wd.spec}-{run_id}-{test}-{result['line']}.json").write_text(json.dumps(body, indent=1), encoding="utf-8")
    resp = svc.post("/v1/heal", body)
    candidates: List[Dict[str, Any]] = []
    on_target = None
    while resp["status"] == "try":
        cand = resp["candidate"]
        candidates.append({"code": cand["code"], "strategy": cand["strategy"], "tier_movement": cand["tier_movement"]})
        patched_line = line_of(source, cand["patch"]["span"]["start"])
        wd.write(apply_patches(source, [cand["patch"]] + cand.get("also_patches", [])))
        rerun, statements = wd.run(test, record=True)
        seen = statements.get(f"{wd.test_file}:{patched_line}") or {}
        count, hit = seen.get("count"), ref_in(items, seen.get("element"))
        if count == 1 and hit is None and seen.get("element"):      # say which facts kept the hit from matching the listing
            el = seen["element"]
            near = [i for i in items if i.get("tag") == el.get("tag") and i.get("name") == el.get("name")]
            row.setdefault("hit_notes", []).append(
                {k: [el.get(k), near[0].get(k)] for k in set(el) | set(near[0])
                 if k not in LOOSE_SKIP and el.get(k) != near[0].get(k)} if near else {"element": el})
        outcome = rerun_outcome(rerun[test], patched_line) if count == 1 else None
        payload: Dict[str, Any] = {"count": count, "test_outcome": outcome}
        if hit:
            payload["hit"] = {"ref": hit}
        if outcome == "failed_later":
            res = rerun[test]
            payload["later"] = {"error": res["error"][:4000], "file": res["frames"][0][0], "line": res["line"],
                                "stack": [{"file": f, "line": n} for f, n in res["frames"]]}
        resp = svc.post(f"/v1/heals/{resp['heal_id']}/result", payload)
        if outcome == "passed" or (outcome == "failed_later" and resp["status"] == "healed"):
            wd.last, on_target = rerun[test], hit is not None and hit == target_ref
        else:
            wd.write(source)
    record_row = svc.get(f"/v1/heals/{resp['heal_id']}")
    seen = (record_row.get("evidence") or {}).get("vision") or {}
    row.update({"images": resp["usage"].get("images", 0), "vision_ran": bool(seen.get("ran")),
                "vision_changed": bool(seen.get("changed")), "vision_trigger": seen.get("trigger") if seen.get("ran") else None,
                "vision_skipped": seen.get("skipped")})
    row.update({"heal_id": resp["heal_id"], "status": resp["status"], "reason": resp.get("reason"),
                "why": resp.get("why"), "settled_at": resp.get("settled_at"), "ended_at": resp.get("ended_at"),
                "calls": resp["usage"]["calls"], "input_tokens": resp["usage"]["input_tokens"],
                "output_tokens": resp["usage"]["output_tokens"], "rounds": resp["round"],
                "tries": [t["result"] for t in record_row["tries"]], "candidates": candidates,
                "duration_ms": record_row.get("duration_ms"),
                "wrong_catches": sum(1 for t in record_row["tries"] if t["result"] in ("wrong_element", "rerun_failed")),
                "tier_movement": record_row.get("tier_movement"), "below_floor": bool(record_row.get("below_floor")),
                "branch": bool(record_row.get("branch")), "cache_hit": bool(record_row.get("cache_hit")),
                "final_locator": record_row.get("final_locator") if resp["status"] == "healed" else None,
                "on_target": on_target if resp["status"] == "healed" else None})
    row["met"] = meets(row)
    return row


def meets(row: Dict[str, Any]) -> bool:
    """Whether the row did what its expectation says (outcome, and each detail the expectation names)."""
    exp = row["expected"]
    if exp.get("outcome") == "unlisted" or row["status"] != exp.get("outcome"):
        return False
    if row["status"] == "healed" and not row["on_target"]:
        return False
    return all(row.get(k) == v for k, v in exp.items() if k in ("settled_at", "tier_movement", "below_floor", "branch"))


def human_fix(source: str, step: Optional[Dict[str, Any]], line: int, old_key: str = "old", new_key: str = "new") -> Optional[str]:
    """What a person would do after a needs_human: the golden replacement written over the broken expression (the line is
    blanked when the element is gone), so the rest of the test still runs and its other drifts are exercised. None when the
    golden file has nothing for the line."""
    if not step:
        return None
    lines = source.split("\n")
    if not step[old_key] or step[old_key] not in lines[line - 1]:
        return None
    if step.get("new") is None:
        lines[line - 1] = ""
    elif not step[new_key]:
        return None                                           # a replacement with no form in this language
    else:
        lines[line - 1] = lines[line - 1].replace(step[old_key], step[new_key])
    return "\n".join(lines)


# ---- the run ---------------------------------------------------------------------------------------------------------
def run_variant(svc: Service, spec: str, variant: str, steps: List[Dict[str, Any]], base_url: str, probe_path: Path,
                fingerprints: Dict[str, Any], tests: List[str], timeout_ms: int, use_fingerprints: bool,
                gaps: Optional[List[Dict[str, Any]]] = None, vision: str = "off", ts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`ts` is set for --client ts: {project, base_url, titles}; the suite, the failure and the heal are then the TypeScript client's."""
    gaps = gaps or []
    wd: Any = (ts_client.TsWorkdir(ts["project"].copy(), base_url, ts["titles"]) if ts
               else Workdir(spec, base_url, probe_path, timeout_ms, steps))
    old_key, new_key = (wd.old_key, wd.new_key) if ts else ("old", "new")
    if vision != "off":
        wd.env["HEAL_TESTBED_SCREENSHOT"] = "1"
    rows: List[Dict[str, Any]] = []
    finals: Dict[str, str] = {}
    started = time.time()
    try:
        for test in tests:
            wd.last, needed_human = None, False
            for _ in range(MAX_STEPS_PER_TEST):
                res = wd.last or wd.run(test)[0][test]
                wd.last = None
                if res["result"] == "passed":
                    finals[test] = "passed after a human fix" if needed_human else "passed"
                    break
                if needed_human and ("AssertionError" in res["error"] or ts_client.is_assertion(res["error"])):
                    finals[test] = "needs_human"           # an assertion that depended on a step a human had to drop
                    break
                dump = wd.failure(test)
                statement = wd.line_text(res["line"])
                step = next((s for s in steps if s[old_key] and s[old_key] in statement and s.get("test") in (None, test)), None)
                if dump is None or "probe" not in dump or not res["frames"]:
                    finals[test] = "no_evidence"
                    break
                row = (ts_client.heal_step(svc, wd, test, res, step, use_fingerprints, f"{spec}-{variant}", meets) if ts
                       else heal_step(svc, wd, dump, test, res, step, fingerprints, f"{spec}-{variant}", use_fingerprints, vision))
                row["step_index"] = steps.index(step) if step else None
                row["golden_target"] = (step.get("target_ts" if ts else "target") if step else None)   # what a bench compares each pick with
                # with vision on, a step the gates refuse can end on another gate's code (the vision call takes the place of the
                # second text call, so `no_pick` from the scripted text repeat becomes the refusal of the vision pick): the
                # recorded gap is the outcome then, and the row says which reason it got
                gap = next((g for g in gaps if step and g["old"] == step["old"] and g["outcome"] == row["status"] == "needs_human"
                            and (vision != "off" or g.get("reason") == row.get("reason"))), None)
                if step is None and row["status"] in ("not_drift", "needs_human") and re.match(r"(await\s+)?expect\(", row["statement"]):
                    # a locator that still matched the wrong element: the failure is the next assertion, nothing is healed
                    row["met"], row["known_gap"] = True, GAP_NOTES["assertion_mismatch"]
                elif gap and not row["met"]:
                    row["met"], row["known_gap"] = True, gap.get("note")
                    if gap.get("reason") != row.get("reason"):
                        row["known_gap"] = f"{gap.get('note')} (recorded as {gap.get('reason')}; with vision it ended {row.get('reason')})"
                elif row["met"] and any(step and g["old"] == step["old"] for g in gaps):
                    row["gap_closed"] = True
                rows.append(row)
                if row["status"] == "healed":
                    continue
                fixed = human_fix(wd.text, step, res["line"], old_key, new_key) if row["status"] == "needs_human" else None
                if fixed is None:
                    finals[test] = row["status"]
                    break
                wd.write(fixed)
                needed_human = True
                row["human_fixed"] = True
            else:
                finals[test] = "step_limit"
    finally:
        shutil.rmtree(wd.dir, ignore_errors=True)
    healed = [r for r in rows if r["status"] == "healed"]
    return {
        "spec": spec, "variant": variant, "seconds": round(time.time() - started, 1), "tests": finals, "rows": rows,
        "summary": {
            "steps_expected": len(steps), "steps_run": len(rows), "healed": len(healed),
            "needs_human": sum(r["status"] == "needs_human" for r in rows), "not_drift": sum(r["status"] == "not_drift" for r in rows),
            "settled_at": {str(k): sum(1 for r in healed if r["settled_at"] == k) for k in ((2, 3, 4) if vision != "off" else (2, 3))},
            "model_calls": sum(r["calls"] for r in rows), "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            "wrong_catches": sum(r["wrong_catches"] for r in rows),
            "wrong_kept": sum(1 for r in healed if not r["on_target"]),
            "images": sum(r["images"] for r in rows), "vision_ran": sum(r["vision_ran"] for r in rows),
            "vision_changed": sum(r["vision_changed"] for r in rows),
            "settled_by_vision": sum(1 for r in healed if r["settled_at"] == 4),
            "tests_passed": sum(1 for v in finals.values() if v == "passed"), "tests_total": len(finals),
            "known_gaps": sum(1 for r in rows if r.get("known_gap")), "gaps_closed": sum(1 for r in rows if r.get("gap_closed")),
            "all_met": all(r["met"] for r in rows) and (spec != "drift" or
                                                         {r.get("step_index") for r in rows} >= set(range(len(steps))))}}


def markdown(report: Dict[str, Any]) -> str:
    vision = report.get("vision", "off") != "off"
    out = [f"# Testbed acceptance run ({report['model']}, vision {report.get('vision', 'off')})", "",
           f"Service: {report['service']}. Action timeout {report['timeout_ms']} ms. Wall time {report['seconds']} s.", "",
           "| spec | variant | steps | healed | needs_human | settled at " + ("2 / 3 / 4" if vision else "2 / 3")
           + " | model calls | tokens in / out | " + ("images | vision ran / changed | " if vision else "")
           + "wrong-element catches | wrong kept | tests passed | as expected |",
           "|---|---|---|---|---|---|---|---|" + ("---|---|" if vision else "") + "---|---|---|---|"]
    for v in report["variants"]:
        s = v["summary"]
        settled = " / ".join(str(s["settled_at"][k]) for k in (("2", "3", "4") if vision else ("2", "3")))
        out.append(f"| {v['spec']} | {v['variant']} | {s['steps_run']}/{s['steps_expected']} | {s['healed']} | {s['needs_human']} | "
                   f"{settled} | {s['model_calls']} | {s['input_tokens']} / {s['output_tokens']} | "
                   + (f"{s['images']} | {s['vision_ran']} / {s['vision_changed']} | " if vision else "")
                   + f"{s['wrong_catches']} | {s['wrong_kept']} | {s['tests_passed']}/{s['tests_total']} | "
                   f"{'yes' if s['all_met'] else 'NO'} |")
    out += ["", "## Steps", "", "| variant | test | line | expected | got | settled | calls | tier | branch | met |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for v in report["variants"]:
        for r in v["rows"]:
            out.append(f"| {v['variant']} | {r['test']} | {r['line']} | {r['expected'].get('outcome')} | {r['status']}"
                       f"{' (' + r['reason'] + ')' if r.get('reason') else ''} | {r['settled_at']} | {r['calls']} | "
                       f"{r['tier_movement'] or ''} | {'yes' if r['branch'] else ''} | {'yes' if r['met'] else 'NO'} |")
    return "\n".join(out) + "\n"


def serve_sites() -> Tuple[http.server.ThreadingHTTPServer, str]:
    os.environ["QA_SERVE_QUIET"] = "1"
    handler = functools.partial(serve.NoCacheHandler, directory=serve.SITE)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def collect_tests(spec: str) -> List[str]:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", SPECS[spec]["file"]],
                         cwd=PY_DIR, capture_output=True, text=True, timeout=120).stdout
    return [m.group(1) for m in re.finditer(r"::(\w+)\s*$", out, re.M)]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run the testbed suites through the heal service and report per variant.")
    ap.add_argument("--model", choices=["fake", "env"], default="fake", help="fake: scripted picks, offline; env: the service's HEAL_TEXT_* model")
    ap.add_argument("--spec", nargs="*", default=DEFAULT_SPECS, choices=sorted(SPECS))
    ap.add_argument("--variants", nargs="*", default=None, help="only these variants")
    ap.add_argument("--service-url", default=os.environ.get("HEAL_SERVICE_URL"), help="drive a running service instead of an in-process one")
    ap.add_argument("--fingerprints", choices=["on", "off"], default="on",
                    help="on: send the base run's fingerprint (step 2 can settle); off: every heal starts at the text pick")
    ap.add_argument("--vision", choices=["off", "auto", "on"], default="off",
                    help="the client's HEAL_VISION: off sends no screenshot at all; auto / on send one viewport screenshot and the "
                         "service may use it (see core.py)")
    ap.add_argument("--candidate-cap", type=int, default=None, help="(fake model) lines in the pick list; default is the service's own")
    ap.add_argument("--max-calls", type=int, default=None, help="(fake model) model calls per heal; default is the service's own")
    ap.add_argument("--write-gaps", action="store_true",
                    help="record this run's misses as the known gaps in acceptance-gaps.json (fake model, default settings; review the diff)")
    ap.add_argument("--client", choices=["python", "ts"], default="python",
                    help="python: this script builds the requests itself; ts: the TypeScript client (playwright-ai-framework-ts) does "
                         "(its Healer, probe and fingerprint fixture, run by testbed/ts_step.mjs), judged by the same rules")
    ap.add_argument("--ts-client-dir", default=os.environ.get("HEAL_TS_CLIENT_DIR"),
                    help="--client ts: the TypeScript client's repository (npm ci done); default $HEAL_TS_CLIENT_DIR")
    ap.add_argument("--timeout-ms", type=int, default=1500, help="action timeout of the suites (default 1500)")
    ap.add_argument("--out", default=str(HERE / ".runs" / "acceptance"), help="directory for acceptance.json and acceptance.md")
    args = ap.parse_args(argv)
    if args.service_url and args.model == "fake":
        args.model = "env"
    ts_mode = args.client == "ts"
    if ts_mode:
        if not args.ts_client_dir or not (Path(args.ts_client_dir) / "node_modules").is_dir():
            raise SystemExit("run_acceptance: --client ts needs --ts-client-dir (or $HEAL_TS_CLIENT_DIR): the TypeScript client's repository with `npm ci` done")
        if args.vision != "off" or args.service_url:
            raise SystemExit("run_acceptance: --client ts runs with --vision off against the in-process service")
    svc = Service(args.model, args.service_url, args.candidate_cap, args.vision, args.max_calls)
    scrub_model_credentials(os.environ)
    app_site = None
    if ts_mode:
        svc.serve_http()
        app_site = ts_client.AppSite(Path(serve.SITE), serve.NoCacheHandler)
    probe_js = svc.get("/v1/probe.js")
    out_dir = Path(args.out).resolve()                     # the suites run from a temp dir: a relative path would break
    out_dir.mkdir(parents=True, exist_ok=True)
    probe_path = out_dir / "probe.js"
    probe_path.write_text(probe_js, encoding="utf-8")
    server, root = serve_sites()
    started = time.time()
    use_fp = args.fingerprints == "on"
    gaps = load_gaps()
    report: Dict[str, Any] = {"client": args.client, "model": args.model, "service": svc.label, "timeout_ms": args.timeout_ms,
                              "fingerprints": args.fingerprints, "vision": args.vision, "variants": []}
    bad = 0
    try:
        for spec in args.spec:
            golden = load_golden(spec)
            ts_ctx: Optional[Dict[str, Any]] = None
            if ts_mode:
                # the TS suite at the one URL /app/: the base site first (a clean run records the fingerprints), then each variant
                project = ts_client.TsProject(Path(args.ts_client_dir), spec, args.timeout_ms)
                titles = {ts_client.test_name(t): t for t in project.titles()}
                tests, app_url = list(titles), f"{app_site.origin}/app/"
                app_site.show(SPECS[spec]["base"])
                base = ts_client.TsWorkdir(project, app_url, titles)
                ts_ctx = {"project": project, "base_url": app_url, "titles": titles}
                fingerprints = {}
            else:
                tests = collect_tests(spec)
                base = Workdir(spec, f"{root}/{SPECS[spec]['base']}/", probe_path, args.timeout_ms)
            try:
                res, recorded = base.run(record=True)
                fingerprints = recorded if not ts_mode else fingerprints
            finally:
                if not ts_mode:
                    shutil.rmtree(base.dir, ignore_errors=True)
            if any(r["result"] != "passed" for r in res.values()):
                print(f"{spec}: the base site is not green: {[n for n, r in res.items() if r['result'] != 'passed']}")
                bad += 1
                continue
            for variant, steps in golden.items():
                if args.variants and variant not in args.variants:
                    continue
                known = [] if (args.model != "fake" or args.candidate_cap or not use_fp) else gaps.get(f"{spec}/{variant}", [])
                if ts_mode:
                    app_site.show(variant)
                result = run_variant(svc, spec, variant, steps, ts_ctx["base_url"] if ts_ctx else f"{root}/{variant}/", probe_path,
                                     fingerprints, tests, args.timeout_ms, use_fp, [dict(g, old=g["old"]) for g in known], args.vision,
                                     ts_ctx)
                if args.write_gaps:
                    gaps[f"{spec}/{variant}"] = [
                        {"old": steps[r["step_index"]]["old"], "outcome": r["status"], "reason": r.get("reason"),
                         "note": GAP_NOTES.get(r.get("reason") or "", "")}
                        for r in result["rows"]
                        if r["status"] == "needs_human" and r.get("step_index") is not None and (not r["met"] or r.get("known_gap"))]
                report["variants"].append(result)
                s = result["summary"]
                bad += (not s["all_met"]) + s["wrong_kept"]
                print(f"{spec:7} {variant:12} steps {s['steps_run']}/{s['steps_expected']} healed {s['healed']} "
                      f"needs_human {s['needs_human']} calls {s['model_calls']} tokens {s['input_tokens']}/{s['output_tokens']} "
                      f"tests {s['tests_passed']}/{s['tests_total']} as_expected={s['all_met']} ({result['seconds']}s)"
                      + (f" | images {s['images']} vision ran {s['vision_ran']} changed {s['vision_changed']} "
                         f"wrong_kept {s['wrong_kept']}" if args.vision != "off" else ""), flush=True)
    finally:
        server.shutdown()
        if app_site is not None:
            app_site.close()
    if args.write_gaps:
        GAPS_FILE.write_text(json.dumps(gaps, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", GAPS_FILE)
    report["seconds"] = round(time.time() - started, 1)
    if not args.service_url:       # an in-process service holds this run only: its own totals (cost, latency, outcomes by model)
        m = svc.get("/v1/metrics")
        report["service_metrics"] = {k: m[k] for k in ("model_calls", "input_tokens", "output_tokens", "cost", "latency",
                                                       "cost_by_model", "outcomes_by_model", "outcomes_without_model")}
    (out_dir / "acceptance.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "acceptance.md").write_text(markdown(report), encoding="utf-8")
    print(f"\nwrote {out_dir / 'acceptance.json'} and {out_dir / 'acceptance.md'}")
    print("acceptance:", "OK" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
