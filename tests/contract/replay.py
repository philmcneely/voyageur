"""
===============================================================================
Contract Replay (drive a fixture through the API with a scripted model)
===============================================================================
The one place that knows how to replay a contract case (fixtures/contract/<case>/, format in fixtures/contract/README.md): load
its request for a language, stand up the service in process with a scripted model, send POST /v1/heal, send each recorded client
result in turn, and boil every response down to the fields a client's behaviour depends on (the summary). The replay test asserts
the summaries equal expected.json exactly; build_fixtures.py uses the same function to write expected.json, which is then
reviewed by a person before it is committed. A client in another language replays the same files against a service it starts and
must arrive at the same summaries (code differs per language; every decision field must be equal).

Features:
    ✓ summarize(): status, round, reason, the step that settled and ended the heal, cache hit, model usage and, for a candidate,
      its strategy, tier, tier movement, floor flag, neutral locator, code, branches, expectation and patch text; never the
      heal id, the prose `why` or a diff, which differ run to run or are not part of the contract
    ✓ ScriptedModel: answers each call from the case's script (reply text and fixed usage), records every prompt it was sent
    ✓ run_case(): all steps in order, the first for the heal, one more for each client result the script names

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi.testclient import TestClient

from voyageur.app import create_app
from voyageur.config import ServiceSettings
from voyageur.core import HealSession
from voyageur.llm import Endpoint, Usage
from voyageur.store import Store
from voyageur.transport import CallResult

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "fixtures" / "contract"
LANGUAGES = ("python", "typescript")
ENDPOINT = Endpoint(base_url="http://localhost:11434/v1", model="scripted-fixture-model")


def request_file(language: str) -> str:
    return "request.json" if language == "python" else "request.typescript.json"


def case_dirs() -> List[Path]:
    return sorted(p for p in CASES.iterdir() if p.is_dir() and (p / "request.json").exists())


class ScriptedModel:
    """A model that answers from the case's script: {"reply": text or null, "usage": [input, output]} per call."""

    def __init__(self, script: List[Dict[str, Any]]) -> None:
        self.script = list(script)
        self.prompts: List[str] = []

    def call(self, endpoint: Any, prompt: str, images: Any = (), heal_id: str = "") -> CallResult:
        self.prompts.append(prompt)
        step = self.script.pop(0) if self.script else {"reply": None, "usage": [0, 0]}
        text = step.get("reply")
        return CallResult(text, "" if text else "no answer", Usage(*step.get("usage", [0, 0])), 1, True)


def _patch(p: Dict[str, Any]) -> Dict[str, Any]:
    return {"file": p["file"], "from": p["from"], "to": p["to"]}


def summarize(resp: Dict[str, Any]) -> Dict[str, Any]:
    """The contract view of one response: everything a client's behaviour depends on, nothing run-specific."""
    out: Dict[str, Any] = {
        "status": resp["status"], "round": resp["round"], "reason": resp.get("reason"), "settled_at": resp.get("settled_at"),
        "ended_at": resp.get("ended_at"), "cache_hit": resp["cache_hit"],
        "usage": {"calls": resp["usage"]["calls"], "input_tokens": resp["usage"]["input_tokens"],
                  "output_tokens": resp["usage"]["output_tokens"]}}
    c: Optional[Dict[str, Any]] = resp.get("candidate")
    if c:
        out["candidate"] = {
            "target_ref": c["target_ref"], "strategy": c["strategy"], "tier": c["tier"], "tier_movement": c["tier_movement"],
            "below_floor": c["below_floor"], "locator": c["locator"], "code": c["code"], "branches": c["branches"],
            "expect": c["expect"], "patch": _patch(c["patch"]), "also_patches": [_patch(p) for p in c["also_patches"]]}
    return out


def run_case(case: Path, language: str, prompts: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Replay `case` for `language`: the summaries of the response to the request and to each recorded client result."""
    script = json.loads((case / "script.json").read_text(encoding="utf-8"))
    model = ScriptedModel(script.get("model", []))
    settings = ServiceSettings(retention_days=14, **script.get("settings", {}))
    client = TestClient(create_app(HealSession(settings, Store(":memory:", 14), model, ENDPOINT), None), base_url="http://127.0.0.1", headers={"Content-Type": "application/json"})
    request = json.loads((case / request_file(language)).read_text(encoding="utf-8"))
    r = client.post("/v1/heal", json=request)
    assert r.status_code == 200, r.text
    resp = r.json()
    steps = [summarize(resp)]
    for result in script.get("results", []):
        assert resp["status"] == "try", f"the script has a result but the heal is already {resp['status']}"
        r = client.post(f"/v1/heals/{resp['heal_id']}/result", json=result)
        assert r.status_code == 200, r.text
        resp = r.json()
        steps.append(summarize(resp))
    if prompts is not None:
        prompts.extend(model.prompts)
    return steps
