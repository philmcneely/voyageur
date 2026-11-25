"""Sample heal records for the dashboard tests and screenshots: a few weeks of runs against an imaginary shop app, with every
kind of outcome, local and cloud model calls, PR follow-ups and one record whose texts are hostile markup. Deterministic."""
import random
import socket
import threading
import time
from typing import Any, Dict, Iterator, Tuple

from fastapi import FastAPI

from voyageur.app import create_app
from voyageur.config import ServiceSettings
from voyageur.core import HealSession
from voyageur.store import Store
from tests.unit.scenarios import FakeModel

TOKEN = "dash-test-tok"
DAY = 86400
HOSTILE = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>'
PAGES = [("pages/login_page.py", "self.page.locator('#username')", "id", "/login"),
         ("pages/login_page.py", "self.page.locator('#password')", "id", "/login"),
         ("pages/cart_page.py", "self.page.get_by_text('Checkout')", "text", "/cart"),
         ("pages/cart_page.py", "self.page.get_by_role('button', name='Apply coupon')", "role", "/cart"),
         ("pages/orders_page.py", "self.page.locator('.order-row .status')", "css", "/orders"),
         ("pages/orders_page.py", "self.page.get_by_label('Search orders')", "label", "/orders"),
         ("pages/profile_page.py", "self.page.get_by_placeholder('Display name')", "placeholder", "/profile")]
KINDS = ["testid", "id", "name", "css", "role", "label", "placeholder", "text"]
REASONS = ["target_missing"] * 5 + ["no_pick"] * 3 + ["budget_exhausted"] * 2 + ["no_stable_locator"] * 2 + ["ambiguous_source", "no_identity_proof"]


def build_store(now: float = None, hostile: bool = True) -> Store:
    """A store with ~70 heals over the last 12 days; `hostile` adds the record whose texts are markup."""
    rnd = random.Random(7)
    now = now or time.time()
    clock = [now - 12 * DAY]
    store = Store(":memory:", 14, lambda: clock[0])
    n = 0
    runs = [f"ci-{1040 + i}" for i in range(9)]
    for ri, run in enumerate(runs):
        clock[0] = now - (12 - ri * 1.4) * DAY
        for _ in range(rnd.randint(5, 10)):
            n += 1
            clock[0] += rnd.randint(40, 900)
            hid = f"h_{n:04d}{rnd.randint(0, 9999):04d}"
            f, call, kind, path = rnd.choice(PAGES)
            drift = rnd.random() > 0.1
            roll = rnd.random()
            outcome = "not_drift" if not drift else "healed" if roll < 0.68 else "needs_human" if roll < 0.95 else "try"
            fields: Dict[str, Any] = dict(
                run_id=run, repo="shop-e2e", framework="playwright-ai-framework", language="python",
                test_id=f"tests/test_{path.strip('/')}.py::test_{rnd.choice(['happy_path', 'guest', 'refund', 'filters'])}",
                file=f, line=rnd.randint(8, 60), page_path=path, page_language=rnd.choice(["en", "en", "fr"]),
                broken_locator=call, broken_kind=kind, broken_tier=KINDS.index(kind), locator_key=f"{f}|{call}",
                drift=int(drift), outcome=outcome, mode="heal", rounds=0, candidates_tried=0, wrong_element=0, covers=1,
                cache_hit=0, budget_exhausted=0, duration_ms=rnd.randint(30, 4200))
            if outcome == "not_drift":
                fields.update(reason="no_locator_in_error", ended_at=1, duration_ms=rnd.randint(2, 20))
            elif outcome == "needs_human":
                reason = rnd.choice(REASONS)
                fields.update(reason=reason, ended_at=3, why=f"{reason.replace('_', ' ')}", budget_exhausted=int(reason == "budget_exhausted"))
            elif outcome == "healed":
                moved = rnd.choices(["up", "same", "down"], [6, 3, 1])[0]
                final = KINDS[max(0, KINDS.index(kind) - 1)] if moved == "up" else kind if moved == "same" else "text"
                settled = rnd.choices([2, 3, 4], [4, 12, 1])[0]
                fields.update(settled_at=settled, ended_at=7, rounds=rnd.randint(1, 2), candidates_tried=rnd.randint(1, 2),
                              wrong_element=int(rnd.random() < 0.12), covers=rnd.choice([1, 1, 1, 2, 4]), tier_movement=moved,
                              final_kind=final, final_tier=KINDS.index(final), final_locator=f"self.page.get_by_test_id('{path.strip('/')}-{n}')",
                              verify_count=1, verify_ref="e3", verify_passed=1, branch=int(rnd.random() < 0.1),
                              cache_hit=int(rnd.random() < 0.15), page_language=rnd.choice(["en", "en", "fr"]))
                if ri < 6:
                    state = rnd.choices(["merged_unchanged", "edited", "reverted", "closed_unmerged", "open"], [14, 2, 1, 1, 1])[0]
                    old = ri < 4
                    fields.update(pr_state=state, failed_again=int(old and rnd.random() < 0.15), followup_at=clock[0] + DAY,
                                  runs_since=rnd.randint(10, 24) if old else rnd.randint(1, 8),
                                  days_since=rnd.randint(30, 40) if old else rnd.randint(2, 20))
            store.create_heal(hid, **fields)
            if outcome in ("healed", "needs_human") and settled_by_model(fields):
                for step in ([3] if rnd.random() < 0.9 else [3, 4]):
                    cloud = rnd.random() < 0.18
                    role = "vision" if step == 4 else "text"
                    model = "qwen2.5-vl:7b" if role == "vision" else "claude-haiku-5-5" if cloud else "qwen3:8b"
                    store.add_call(hid, role=role, model=model, local=not cloud and role != "vision" or role == "vision",
                                   input_tokens=rnd.randint(380, 1500), output_tokens=rnd.randint(8, 64),
                                   images=1 if role == "vision" else 0, latency_ms=rnd.randint(300, 6000), ok=True, step=step)
            if outcome == "healed":
                for r in range(1, fields["candidates_tried"] + 1):
                    store.add_try(hid, r, "e3", fields["final_kind"], fields["final_locator"],
                                  "wrong_element" if (r == 1 and fields["wrong_element"]) else "ok", "count=1 hit=e3 outcome=passed")
    clock[0] = now - 600
    for _ in range(4):                               # advise requests from an agent
        n += 1
        store.create_heal(f"h_adv{n:05d}", run_id="mcp", repo="shop-e2e", framework="mcp", language="python", test_id="agent",
                          file="pages/cart_page.py", line=22, broken_locator="self.page.locator('#coupon')", broken_kind="id",
                          locator_key="pages/cart_page.py|self.page.locator('#coupon')", drift=1, outcome="advised", mode="advise",
                          ended_at=5, final_kind="testid", final_tier=0, tier_movement="up", duration_ms=900, settled_at=3)
        store.add_call(f"h_adv{n:05d}", role="text", model="qwen3:8b", local=True, input_tokens=700, output_tokens=20, images=0,
                       latency_ms=1200, ok=True, step=3)
    clock[0] = now - 300                              # one record whose texts are hostile markup
    if not hostile:
        clock[0] = now
        return store
    store.create_heal("h_hostile0000000001", run_id=f"ci-{HOSTILE[:10]}", repo="shop-e2e", framework="playwright-ai-framework",
                      language="python", test_id=f"tests/test_{HOSTILE}.py::t", file="pages/x_page.py", line=3, page_path="/x",
                      broken_locator=f"self.page.locator('{HOSTILE}')", broken_kind="css", broken_tier=3,
                      locator_key=f"pages/x_page.py|self.page.locator('{HOSTILE}')", drift=1, outcome="needs_human",
                      reason="target_missing", why=HOSTILE, ended_at=3, mode="heal", duration_ms=77, page_language="en",
                      final_locator=HOSTILE, evidence={"files": 1, "inventory": 4, HOSTILE: HOSTILE})
    store.add_try("h_hostile0000000001", 1, "e1", "css", HOSTILE, "wrong_element", HOSTILE)
    store.add_call("h_hostile0000000001", role="text", model=HOSTILE, local=True, input_tokens=5, output_tokens=1, images=0,
                   latency_ms=5, ok=True, step=3)
    clock[0] = now
    return store


def settled_by_model(fields: Dict[str, Any]) -> bool:
    return fields.get("settled_at") != 2


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_app(store: Store, token: str = TOKEN, prices: bool = True, retention_days: int = 14) -> FastAPI:
    prices_map = {"qwen3:8b": (0.0, 0.0), "qwen2.5-vl:7b": (0.0, 0.0), "claude-haiku-5-5": (1.0, 5.0)} if prices else {}
    core = HealSession(ServiceSettings(token=token, prices=prices_map, retention_days=retention_days), store, FakeModel())
    return create_app(core, token)


def serve(app: FastAPI) -> Iterator[Tuple[str, Any]]:
    import uvicorn
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", server
    finally:
        server.should_exit = True
        thread.join(5)
