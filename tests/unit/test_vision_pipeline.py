"""Vision in the heal ladder: when it runs, what it may change, what it may never do, and what the record keeps."""
import base64
import copy
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from voyageur.app import create_app
from voyageur.config import ServiceSettings
from voyageur.core import HealSession
from voyageur.llm import Endpoint, Usage
from voyageur.schemas import HealRequest, ResultRequest
from voyageur.store import Store
from voyageur.transport import CallResult, Transport
from voyageur.vision import parse_mark, twins_of
from tests.unit.scenarios import ENDPOINT, request
from tests.unit.test_screenshot_overlay import shot

pytestmark = pytest.mark.unit

VISION = Endpoint(base_url="http://localhost:11434/v1", model="vision-model", images_allowed=True)
TOKEN = {"Authorization": "Bearer t0k"}
LOGIN = [  # two fields a person cannot tell apart by their words: same name and label, only the position differs
    {"ref": "e0", "tag": "input", "type": "text", "role": "textbox", "name": "Username", "label": "Username",
     "attrs": {"id": "username-a"}, "box": {"x": 20, "y": 20, "w": 200, "h": 30}},
    {"ref": "e1", "tag": "input", "type": "text", "role": "textbox", "name": "Username", "label": "Username",
     "attrs": {"id": "username-b"}, "box": {"x": 20, "y": 90, "w": 200, "h": 30}},
]
DISTINCT = [  # nothing alike: no close call
    {"ref": "e0", "tag": "input", "type": "text", "role": "textbox", "name": "Username", "label": "Username",
     "attrs": {"id": "username"}, "box": {"x": 20, "y": 20, "w": 200, "h": 30}},
    {"ref": "e1", "tag": "input", "type": "text", "role": "textbox", "name": "Nickname", "label": "Nickname",
     "attrs": {"id": "nick"}, "box": {"x": 20, "y": 90, "w": 200, "h": 30}},
]
NEARBY = [  # two fields that both plausibly are `#username`, with different copy (not a close call)
    {"ref": "e0", "tag": "input", "type": "text", "role": "textbox", "name": "Username", "label": "Username",
     "attrs": {"id": "username"}, "box": {"x": 20, "y": 20, "w": 200, "h": 30}},
    {"ref": "e1", "tag": "input", "type": "text", "role": "textbox", "name": "Username again", "label": "Username again",
     "attrs": {"id": "username-again"}, "box": {"x": 20, "y": 90, "w": 200, "h": 30}},
]
UNPROVEN = [  # e0 is a field with nothing in common with `#username`: a pick of it is refused for lack of proof
    {"ref": "e0", "tag": "input", "type": "text", "role": "textbox", "name": "Zip", "label": "Zip",
     "attrs": {"id": "zip"}, "box": {"x": 20, "y": 20, "w": 200, "h": 30}},
    {"ref": "e1", "tag": "input", "type": "text", "role": "textbox", "name": "Username", "label": "Username",
     "attrs": {"id": "username-main"}, "box": {"x": 20, "y": 90, "w": 200, "h": 30}},
]


class Scripted:
    """A model client that answers text calls and vision calls from two lists and keeps what it was sent."""

    def __init__(self, text=(), vision=()):
        self.text, self.vision, self.calls = list(text), list(vision), []

    def call(self, ep, prompt, images=(), heal_id=""):
        self.calls.append({"model": ep.model, "prompt": prompt, "images": list(images), "heal_id": heal_id})
        queue = self.vision if images else self.text
        text = queue.pop(0) if queue else None
        return CallResult(text, "" if text else "no answer", Usage(120 if images else 100, 8), 5, True)

    @property
    def images(self):
        return [i for c in self.calls for i in c["images"]]

    @property
    def vision_calls(self):
        return [c for c in self.calls if c["images"]]


def req(inventory=DISTINCT, vision="auto", picture=True, viewport=True, **over):
    r = request(inventory)
    r["options"]["vision"] = vision
    if picture:
        r["page"]["screenshot"] = shot(w=340, h=240)
    if viewport:
        r["page"]["viewport"] = {"w": 340, "h": 240, "dpr": 1}
    for k, v in over.items():
        r[k] = v
    return HealRequest.model_validate(r)


def core(model, vision=VISION, retention=14, **settings):
    s = ServiceSettings(retention_days=retention, **settings)
    return HealSession(s, Store(":memory:", retention), model, ENDPOINT, vision_endpoint=vision)


def ok(ref):
    return ResultRequest.model_validate({"count": 1, "hit": {"ref": ref}, "test_outcome": "passed"})


def wrong(ref):
    return ResultRequest.model_validate({"count": 1, "hit": {"ref": ref}, "test_outcome": "failed"})


def pick(ref):
    return json.dumps({"ref": ref, "why": "text"})


def mark(n):
    return json.dumps({"mark": n, "why": "the box"})


def vrec(c, r):
    return c.get(r.heal_id)["evidence"].get("vision")


# ---- the modes -------------------------------------------------------------------------------------------------------
def test_off_never_reads_or_sends_an_image_and_adds_nothing_to_the_record():
    m = Scripted([pick("e0")])
    c = core(m)
    r = c.start(req(vision="off", picture=False))
    assert r.status == "try" and r.usage.images == 0 and m.vision_calls == [] and r.settled_at == 3
    assert "vision" not in c.get(r.heal_id)["evidence"] and c.store.get_overlay(r.heal_id) is None


def test_auto_leaves_a_confident_text_pick_alone_and_sends_no_image():
    m = Scripted([pick("e0")])
    c = core(m)
    r = c.start(req(DISTINCT))
    assert r.settled_at == 3 and r.usage.calls == 1 and r.usage.images == 0 and m.images == []
    assert vrec(c, r)["ran"] is False and "skipped" not in vrec(c, r)


def test_auto_runs_vision_on_a_close_call_and_the_vision_pick_replaces_the_text_pick():
    m = Scripted([pick("e0")], [mark(2)])
    c = core(m)
    r = c.start(req(LOGIN))
    assert r.status == "try" and r.candidate.target_ref == "e1" and r.settled_at == 4
    assert r.usage.calls == 2 and r.usage.images == 1
    assert {(u.role, u.model, u.calls, u.images) for u in r.usage.by_model} == {("text", "test-model", 1, 0),
                                                                                  ("vision", "vision-model", 1, 1)}
    v = vrec(c, r)
    assert v["ran"] and v["trigger"] == "close_call" and v["changed"] is True and v["result"] == "picked" and v["images"] == 1
    assert [x["role"] for x in c.get(r.heal_id)["calls"]] == ["text", "vision"]
    assert c.get(r.heal_id)["calls"][1]["step"] == 4 and c.get(r.heal_id)["calls"][1]["images"] == 1
    done = c.result(r.heal_id, ok("e1"))
    assert done.status == "healed" and done.settled_at == 4


def test_auto_vision_confirming_the_text_pick_keeps_step_3():
    m = Scripted([pick("e0")], [mark(1)])
    r = core(m).start(req(LOGIN))
    assert r.candidate.target_ref == "e0" and r.settled_at == 3 and r.usage.calls == 2


def test_auto_runs_vision_when_the_text_model_gives_no_usable_pick():
    m = Scripted(["I think it is the username box"], [mark(1)])
    c = core(m)
    r = c.start(req(DISTINCT))
    assert r.candidate.target_ref == "e0" and r.settled_at == 4 and r.usage.calls == 2
    assert vrec(c, r)["trigger"] == "no_pick" and vrec(c, r)["changed"] is False


def test_auto_runs_vision_when_a_gate_refused_the_text_pick_and_the_refused_element_is_not_offered():
    m = Scripted([pick("e0")], [mark(1)])
    c = core(m)
    r = c.start(req(UNPROVEN))
    assert r.candidate.target_ref == "e1" and r.settled_at == 4 and r.usage.calls == 2
    assert vrec(c, r)["trigger"] == "refused"
    prompt = m.vision_calls[0]["prompt"]
    assert "[1] |" in prompt and '"Zip"' not in prompt and "[2]" not in prompt        # e0 was ruled out: one candidate left


def test_without_vision_a_refused_pick_still_gets_a_second_text_call_as_before():
    m = Scripted([pick("e0"), pick("e1")])
    r = core(m).start(req(UNPROVEN, vision="off", picture=False))
    assert r.candidate.target_ref == "e1" and r.usage.calls == 2 and m.vision_calls == []


def test_auto_runs_vision_after_the_live_check_found_the_wrong_element():
    m = Scripted([pick("e0")], [mark(1)])
    c = core(m)
    r = c.start(req(NEARBY))
    assert r.candidate.target_ref == "e0" and m.vision_calls == []
    r2 = c.result(r.heal_id, wrong("e1"))
    assert m.vision_calls and r2.status == "try" and r2.candidate.target_ref == "e1" and r2.usage.calls == 2
    assert "[1]" in m.vision_calls[0]["prompt"] and "[2]" not in m.vision_calls[0]["prompt"]     # e0 is ruled out
    assert c.get(r.heal_id)["evidence"]["vision"]["trigger"] == "wrong_element"


def test_on_runs_vision_for_every_heal_that_reaches_the_pick_and_a_confirmed_pick_stays():
    m = Scripted([pick("e0")], [mark(1)])
    c = core(m)
    r = c.start(req(DISTINCT, vision="on"))
    assert r.candidate.target_ref == "e0" and r.usage.calls == 2 and r.usage.images == 1 and r.settled_at == 3
    assert vrec(c, r)["trigger"] == "on" and vrec(c, r)["result"] == "picked" and "changed" not in vrec(c, r)


def test_on_lets_a_vision_pick_that_passes_the_gates_replace_the_text_pick_and_keeps_the_text_pick_when_it_does_not():
    m = Scripted([pick("e0")], [mark(2)])
    r = core(m).start(req(LOGIN, vision="on"))
    assert r.candidate.target_ref == "e1" and r.settled_at == 4
    # vision picks the unproven field: refused by the gates, the text pick stands
    m = Scripted([pick("e1")], [mark(2)])         # the list is ranked by the broken call's words: Username is 1, Zip is 2
    c = core(m)
    r = c.start(req(UNPROVEN, vision="on"))
    assert "Zip" in m.vision_calls[0]["prompt"].split("[2]")[1]
    assert r.candidate.target_ref == "e1" and r.settled_at == 3 and vrec(c, r)["result"] == "refused:no_identity_proof"


def test_on_asks_vision_even_when_the_text_model_said_none_and_a_vision_none_changes_nothing():
    m = Scripted([json.dumps({"ref": None, "why": "gone"})], [json.dumps({"mark": None, "why": "gone"})])
    r = core(m).start(req(DISTINCT, vision="on"))
    assert r.status == "needs_human" and r.reason == "target_missing" and r.usage.calls == 2
    m = Scripted([json.dumps({"ref": None, "why": "gone"})], [mark(1)])
    assert core(m).start(req(DISTINCT, vision="on")).status == "try"
    m = Scripted([json.dumps({"ref": None, "why": "gone"})])
    assert core(m).start(req(DISTINCT, vision="auto")).reason == "target_missing"       # auto does not second-guess none


def test_a_heal_never_makes_more_than_one_vision_call_or_two_calls_in_all():
    m = Scripted([pick("e0")], [mark(1), mark(1)])
    c = core(m)
    r = c.start(req(LOGIN, vision="on"))
    c.result(r.heal_id, wrong("e0"))
    assert len(m.vision_calls) == 1 and len(m.calls) <= 2


# ---- what vision may never do ----------------------------------------------------------------------------------------
def test_a_mark_that_is_not_on_the_list_is_refused_and_the_text_pick_stands():
    for bad in (mark(7), mark(0), mark(-1), json.dumps({"mark": "1", "why": ""}), json.dumps({"mark": 1.5, "why": ""}),
                json.dumps({"mark": True, "why": ""}), json.dumps({"mark": 1, "locator": "#x"}), "mark 1", "e1"):
        m = Scripted([pick("e0")], [bad])
        c = core(m)
        r = c.start(req(LOGIN, vision="on"))
        assert r.candidate.target_ref == "e0" and r.settled_at == 3, bad
        assert vrec(c, r)["result"] in ("mark_not_on_list", "not_json"), bad


def test_a_mark_that_is_not_drawn_is_refused():
    inventory = copy.deepcopy(LOGIN)
    inventory[1]["box"] = {"x": 900, "y": 900, "w": 50, "h": 30}          # below the 340 x 240 screenshot
    m = Scripted([pick("e0")], [mark(2)])
    c = core(m)
    r = c.start(req(inventory, vision="on"))
    assert r.candidate.target_ref == "e0" and vrec(c, r)["result"] == "mark_not_visible"
    assert "[2] (not visible in the image)" in m.vision_calls[0]["prompt"]


def test_vision_with_no_text_pick_and_a_bad_mark_ends_as_the_text_failure_did():
    m = Scripted(["no idea"], [mark(9)])
    r = core(m).start(req(DISTINCT))
    assert r.status == "needs_human" and r.reason == "no_pick"


def test_parse_mark_reads_only_a_json_object_with_a_drawn_listed_number():
    assert parse_mark('{"mark": 2, "why": "ok"}', 3, {1, 2}).mark == 2
    assert parse_mark('{"mark": null, "why": "none"}', 3, {1, 2}).none
    assert parse_mark('{"mark": 3, "why": "ok"}', 3, {1, 2}).reason == "mark_not_visible"
    assert parse_mark('{"mark": 4, "why": "ok"}', 3, {1, 2}).reason == "mark_not_on_list"
    assert parse_mark('{"mark": 2, "why": "' + "x" * 201 + '"}', 3, {2}).reason == "not_json"
    assert parse_mark(None, 3, {2}).reason == "not_json"


def test_the_vision_prompt_has_the_marks_the_list_and_the_broken_line_but_no_refs_or_source_file():
    m = Scripted([pick("e0")], [mark(1)])
    core(m).start(req(LOGIN, vision="on"))
    p = m.vision_calls[0]["prompt"]
    assert "[1] | tag=" in p and "[2] | tag=" in p and "e0 |" not in p and "e1 |" not in p
    assert "self.page.locator('#username').fill(user)" in p and "class LoginPage" not in p
    assert '"mark"' in p and "locator" in p.split("Reply with JSON")[0]       # the broken call is shown; the reply is a mark
    (label, png), = m.vision_calls[0]["images"]
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and "numbered" in label


def test_the_vision_pick_is_judged_by_the_same_identity_proof():
    m = Scripted([pick("e1")], [mark(2)])       # vision chooses the unproven Zip field over the proven text pick
    c = core(m)
    r = c.start(req(UNPROVEN, vision="on"))
    assert r.candidate.target_ref == "e1" and vrec(c, r)["result"] == "refused:no_identity_proof"
    assert vrec(c, r).get("changed") in (None, False)


# ---- when vision cannot run ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("endpoint,why", [(Endpoint(), "vision_not_configured"),
                                          (Endpoint(base_url="http://localhost:1/v1", model="v", images_allowed=False),
                                           "images_not_allowed")])
def test_unconfigured_or_not_allowed_vision_falls_back_to_text_and_no_image_leaves(endpoint, why):
    m = Scripted([pick("e0")])
    c = core(m, vision=endpoint)
    r = c.start(req(LOGIN, vision="on"))
    assert r.status == "try" and r.candidate.target_ref == "e0" and r.usage.images == 0
    assert m.images == [] and vrec(c, r)["skipped"] == why and vrec(c, r)["ran"] is False and c.store.get_overlay(r.heal_id) is None


def test_no_screenshot_with_auto_is_text_only_and_says_so():
    m = Scripted([pick("e0")])
    c = core(m)
    r = c.start(req(LOGIN, picture=False))
    assert r.usage.calls == 1 and m.images == [] and vrec(c, r)["skipped"] == "no_screenshot"


def test_no_visible_candidate_is_text_only_and_says_so():
    inventory = copy.deepcopy(LOGIN)
    for item in inventory:
        item["box"] = {"x": 900, "y": 900, "w": 50, "h": 30}
    m = Scripted([pick("e0")])
    c = core(m)
    r = c.start(req(inventory, vision="on"))
    assert r.status == "try" and m.images == [] and vrec(c, r)["skipped"] == "no_visible_candidate"


def test_a_screenshot_that_does_not_fit_the_viewport_is_text_only():
    m = Scripted([pick("e0")])
    c = core(m)
    r = c.start(req(LOGIN, vision="on", viewport=False))
    assert r.status == "try" and m.images == [] and vrec(c, r)["skipped"] == "no_viewport"


def test_the_call_limit_applies_to_vision():
    m = Scripted([pick("e0")], [mark(2)])
    c = core(m, max_calls=1)
    r = c.start(req(LOGIN, vision="on"))
    assert r.candidate.target_ref == "e0" and m.images == [] and vrec(c, r)["skipped"] == "max_calls"


def test_a_spent_run_budget_stops_vision_and_a_bad_text_pick_ends_with_budget_exhausted():
    m = Scripted([pick("e0")], [mark(2)])
    c = core(m, run_token_budget=50)                                  # one text call spends 108 tokens
    r = c.start(req(LOGIN, vision="on"))
    assert r.candidate.target_ref == "e0" and m.images == [] and vrec(c, r)["skipped"] == "budget_exhausted"


def test_vision_tokens_count_against_the_run_budget():
    m = Scripted(["nothing usable"], [mark(1)])
    c = core(m, run_token_budget=10_000)
    r = c.start(req(DISTINCT))
    assert c._spent["run1"] == 108 + 128 and r.usage.input_tokens == 220


# ---- egress: an image goes nowhere the text may not go, and only where it is allowed ---------------------------------
def counting_transport():
    seen = []

    def handler(request_):
        seen.append(request_)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"mark": 1, "why": "x"}'}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    return Transport(httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)), seen


def test_an_endpoint_that_was_not_allowed_images_gets_no_request_at_all():
    t, seen = counting_transport()
    ep = Endpoint(base_url="http://localhost:1/v1", model="v", images_allowed=False)
    out = t.call(ep, "prompt", (("label", b"\x89PNG"),), "h_1")
    assert out.text is None and out.sent is False and "request not built" in out.why and seen == []


def test_an_allowed_image_still_does_not_go_over_plain_http_to_a_public_host():
    t, seen = counting_transport()
    ep = Endpoint(base_url="http://203.0.113.9/v1", model="v", images_allowed=True)
    out = t.call(ep, "prompt", (("label", b"\x89PNG"),), "h_1")
    assert out.text is None and out.sent is False and "plain http" in out.why and seen == []


def test_an_allowed_local_endpoint_gets_the_image_in_the_request():
    t, seen = counting_transport()
    ep = Endpoint(base_url="http://localhost:1/v1", model="v", images_allowed=True)
    out = t.call(ep, "prompt", (("label", b"\x89PNG"),), "h_1")
    body = json.loads(seen[0].content)
    assert out.sent and "data:image/png;base64," in json.dumps(body)


def test_a_vision_call_the_egress_policy_refused_counts_no_image_and_keeps_no_overlay():
    c = core(None)                                                    # model replaced below by the real transport
    t, seen = counting_transport()
    c.model = t
    c.vision_endpoint = Endpoint(base_url="http://203.0.113.9/v1", model="v", images_allowed=True)
    c.endpoint = Endpoint(base_url="http://localhost:1/v1", model="t")
    t2 = Scripted([pick("e0")])

    class Mixed:                                                      # text goes to the fake, the image to the real transport
        def call(self, ep, prompt, images=(), heal_id=""):
            return t.call(ep, prompt, images, heal_id) if images else t2.call(ep, prompt, images, heal_id)
    c.model = Mixed()
    r = c.start(req(LOGIN, vision="on"))
    assert r.status == "try" and r.candidate.target_ref == "e0" and r.usage.images == 0 and seen == []
    assert c.store.get_overlay(r.heal_id) is None and vrec(c, r)["images"] == 0


# ---- the record, the overlay and retention ---------------------------------------------------------------------------
def served(model, retention=14, token="t0k", **kw):
    c = core(model, retention=retention, **kw)
    c.settings = ServiceSettings(retention_days=retention, token=token, **kw)
    return c, TestClient(create_app(c))


def test_the_overlay_is_kept_with_the_record_and_served_behind_the_token():
    m = Scripted([pick("e0")], [mark(2)])
    c, http = served(m)
    r = c.start(req(LOGIN))
    assert http.get(f"/v1/heals/{r.heal_id}/overlay.png").status_code == 401
    assert http.get(f"/v1/heals/{r.heal_id}/overlay.png", headers={"Authorization": "Bearer wrong"}).status_code == 401
    got = http.get(f"/v1/heals/{r.heal_id}/overlay.png", headers=TOKEN)
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert got.headers["cache-control"] == "private, no-store" and got.content == m.vision_calls[0]["images"][0][1]
    assert http.get("/v1/heals/h_nope/overlay.png", headers=TOKEN).status_code == 404


def test_a_heal_with_no_vision_call_has_no_overlay():
    m = Scripted([pick("e0")])
    c, http = served(m)
    r = c.start(req(DISTINCT))
    assert http.get(f"/v1/heals/{r.heal_id}/overlay.png", headers=TOKEN).status_code == 404


def test_retention_zero_keeps_no_overlay_and_retention_expires_it():
    m = Scripted([pick("e0")], [mark(2)])
    c, http = served(m, retention=0)
    r = c.start(req(LOGIN))
    assert r.usage.images == 1 and http.get(f"/v1/heals/{r.heal_id}/overlay.png", headers=TOKEN).status_code == 404
    assert c.store.count_rows("overlays") == 0
    clock = {"t": 1000.0}
    store = Store(":memory:", 2, lambda: clock["t"])
    store.create_heal("h1")
    store.put_overlay("h1", b"\x89PNG-bytes")
    assert store.get_overlay("h1") == b"\x89PNG-bytes"
    clock["t"] += 3 * 86400
    assert store.get_overlay("h1") is None and store.count_rows("overlays") == 0 and store.get_heal("h1") is None


def test_an_overlay_goes_with_its_heal_when_the_heal_is_purged():
    clock = {"t": 1000.0}
    store = Store(":memory:", 1, lambda: clock["t"])
    store.create_heal("h1")
    store.put_overlay("h1", b"png")
    clock["t"] += 2 * 86400
    store.purge()
    assert store.count_rows("overlays") == 0


def test_image_bytes_are_in_no_log_line_and_no_stored_row(caplog):
    caplog.set_level(logging.DEBUG)
    m = Scripted([pick("e0")], [mark(2)])
    c = core(m)
    body = req(LOGIN)
    r = c.start(body)
    c.result(r.heal_id, ok("e1"))
    b64 = base64.b64encode(m.vision_calls[0]["images"][0][1]).decode()
    stored = json.dumps(c.get(r.heal_id), default=str)
    assert b64[:60] not in caplog.text and "iVBORw0KGgo" not in caplog.text
    assert "iVBORw0KGgo" not in stored and b64[:60] not in stored
    assert body.page.screenshot.data[:60] not in caplog.text and body.page.screenshot.data[:60] not in stored


def test_the_screenshot_is_dropped_once_vision_has_run_or_the_heal_ends():
    m = Scripted([pick("e0")])
    c = core(m)
    r = c.start(req(DISTINCT))                                    # confident text pick: the image is held for a later round
    assert c._held_images > 0
    c.result(r.heal_id, ok("e0"))
    assert c._held_images == 0 and not c._open
    m = Scripted([pick("e0")], [mark(1)])
    c = core(m)
    r = c.start(req(LOGIN))
    assert c._held_images == 0                                     # vision already ran: nothing is kept


def test_a_bounded_number_of_screenshots_is_held_for_open_conversations(monkeypatch):
    import voyageur.core as core_mod
    monkeypatch.setattr(core_mod, "MAX_HELD_IMAGE_BYTES", 1)
    m = Scripted([pick("e0")], [mark(1)])
    c = core(m)
    r = c.start(req(DISTINCT))
    assert c._held_images == 0 and vrec(c, r)["skipped"] == "image_not_kept"
    r2 = c.result(r.heal_id, wrong("e1"))                          # no image left: the text path as before
    assert m.vision_calls == [] and r2.status in ("try", "needs_human")


def test_the_cache_does_not_serve_a_text_only_refusal_to_a_run_that_can_look():
    m = Scripted([pick("e0"), pick("e0")])
    c = core(m)
    off = c.start(req(UNPROVEN, vision="off", picture=False))     # e0 unproven, both text calls spent: cached refusal
    assert off.status == "needs_human"
    m.text, m.vision = [pick("e0")], [mark(1)]
    auto = c.start(req(UNPROVEN, vision="auto"))
    assert auto.status == "try" and auto.candidate.target_ref == "e1"


# ---- the API and capabilities -----------------------------------------------------------------------------------------
def test_vision_off_with_an_image_is_a_422_over_the_api_and_an_oversize_one_a_413():
    c, http = served(Scripted())
    body = request()
    body["page"]["screenshot"] = shot(w=340, h=240)
    body["options"]["vision"] = "off"
    r = http.post("/v1/heal", json=body, headers=TOKEN)
    assert r.status_code == 422 and "iVBOR" not in r.text
    body["options"]["vision"] = "auto"
    body["page"]["screenshot"]["data"] = "A" * (6 * 1024 * 1024)
    assert http.post("/v1/heal", json=body, headers=TOKEN).status_code == 413
    body["page"]["screenshot"] = shot(b"GIF89a" + b"\x00" * 40, w=340, h=240)
    assert http.post("/v1/heal", json=body, headers=TOKEN).status_code == 422


def test_a_whole_vision_conversation_over_http():
    m = Scripted([pick("e0")], [mark(2)])
    c, http = served(m)
    body = request(LOGIN)
    body["page"]["screenshot"] = shot(w=340, h=240)
    body["page"]["viewport"] = {"w": 340, "h": 240, "dpr": 1, "scroll": {"x": 0, "y": 0}}
    body["options"]["vision"] = "auto"
    r = http.post("/v1/heal", json=body, headers=TOKEN).json()
    assert r["status"] == "try" and r["candidate"]["target_ref"] == "e1" and r["usage"]["images"] == 1
    assert http.get(f"/v1/heals/{r['heal_id']}/overlay.png", headers=TOKEN).status_code == 200
    done = http.post(f"/v1/heals/{r['heal_id']}/result", json={"count": 1, "hit": {"ref": "e1"}, "test_outcome": "passed"},
                     headers=TOKEN).json()
    assert done["status"] == "healed"
    m2 = http.get("/v1/metrics", headers=TOKEN).json()
    assert m2["vision"]["calls"] == 1


def test_capabilities_report_vision_without_an_endpoint_or_a_key():
    c, http = served(Scripted(), token="t0k")
    c.vision_endpoint = Endpoint(base_url="http://secret-host:9/v1", model="vision-model", api_key="sk-secret", images_allowed=True)
    body = http.get("/v1/capabilities", headers=TOKEN).json()
    text = json.dumps(body)
    assert body["vision"] == {"available": True} and body["models"]["vision"]["configured"] is True
    assert body["models"]["vision"]["model"] == "vision-model" and body["models"]["vision"]["images_allowed"] is True
    assert "secret-host" not in text and "sk-secret" not in text and body["limits"]["screenshot_bytes"] == 4 * 1024 * 1024
    c.vision_endpoint = Endpoint(base_url="http://x:9/v1", model="v", images_allowed=False)
    body = http.get("/v1/capabilities", headers=TOKEN).json()
    assert body["vision"] == {"available": False} and body["models"]["vision"]["configured"] is True
    c.vision_endpoint = Endpoint()
    body = http.get("/v1/capabilities", headers=TOKEN).json()
    assert body["vision"] == {"available": False} and body["models"]["vision"]["configured"] is False


# ---- the close call ---------------------------------------------------------------------------------------------------
def test_twins_are_candidates_a_text_reader_cannot_tell_apart():
    a, b, other = LOGIN[0] | {"ref": "e0"}, LOGIN[1] | {"ref": "e1"}, DISTINCT[1]
    flat = [{"ref": e["ref"], "tag": e["tag"], "role": e["role"], "name": e["name"], "label": e["label"],
             "row": e.get("row", "")} for e in (a, b, other)]
    flat[2]["ref"] = "e2"
    assert twins_of(flat[0], flat) == ["e1"] and twins_of(flat[2], flat) == []
    flat[1]["row"] = "row-2"                                       # a row tells them apart
    assert twins_of(flat[0], flat) == []
    flat[1]["row"] = ""
    flat[1]["name"] = "USERNAME "                                  # case and spacing do not
    flat[1]["label"] = "Username"
    assert twins_of(flat[0], flat) == ["e1"]
    assert twins_of({"ref": "e9", "tag": "div"}, flat) == []       # no copy, no twin


def test_any_render_failure_is_a_skipped_overlay_never_a_crash(monkeypatch, caplog):
    from voyageur import stages

    def boom(*a, **k):
        raise MemoryError("worker blew up on a hostile image")
    monkeypatch.setattr(stages, "render", boom)
    m = Scripted([pick("e0")])
    c = core(m)
    r = c.start(req(LOGIN, vision="on"))
    assert r.status == "try" and r.candidate.target_ref == "e0" and m.images == []
    assert vrec(c, r)["skipped"] == "overlay_error"          # decodable, but drawing failed: not the same as undecodable
    logged = [x.getMessage() for x in caplog.records if "overlay failed" in x.getMessage()]
    assert logged == ["overlay failed: MemoryError"]         # the exception type only, never its text or image bytes
