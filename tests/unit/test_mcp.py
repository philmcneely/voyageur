"""The MCP tools: heal_locator (advise mode over the same core) and audit_locators, through the functions and over stdio."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from voyageur import cli
from voyageur.mcp_server import ToolRefused, audit_locators, heal_locator
from tests.unit.scenarios import FakeModel, request, session
from tests.unit.test_audit import body as audit_body
from tests.unit.test_overlay_endpoint import body as overlay_body

pytestmark = pytest.mark.unit
SRC = str(Path(__file__).resolve().parents[2] / "src")
FINGERPRINT = {"fingerprint": {"tag": "input", "attrs": {"name": "user", "id": "username"}}, "language": "en"}


def advise_body(**over):
    return request(last_pass=FINGERPRINT, **over)


def test_heal_locator_runs_in_advise_mode_whatever_the_caller_sent():
    core = session(FakeModel())
    body = advise_body()
    body["options"] = {"mode": "heal", "max_rounds": 2}
    out = heal_locator(core, body)
    assert out["status"] == "advised" and out["advice"]["code"] == 'self.page.get_by_test_id("login-user")'
    assert out["candidate"] is None and "patch" not in json.dumps(out).replace("also_patches", "")
    rec = core.get(out["heal_id"])
    assert rec["mode"] == "advise" and rec["outcome"] == "advised" and core.open_conversations() == 0


def test_heal_locator_uses_the_pick_when_there_is_no_fingerprint():
    core = session(FakeModel('{"ref": "e0", "why": "the username box"}'))
    out = heal_locator(core, request())
    assert out["status"] == "advised" and out["usage"]["calls"] == 1 and out["settled_at"] == 3


def test_heal_locator_refuses_what_the_api_refuses_without_echoing_it():
    core = session(FakeModel())
    typed = advise_body()
    typed["page"]["inventory"][0]["value"] = "hunter2"
    with pytest.raises(ToolRefused) as e:
        heal_locator(core, typed)
    assert "hunter2" not in str(e.value) and "value" in str(e.value)
    with pytest.raises(ToolRefused, match="cookies"):
        heal_locator(core, {**advise_body(), "cookies": []})
    with pytest.raises(ToolRefused, match="schema"):
        heal_locator(core, {"client": {"framework": "x"}})
    too_many = advise_body()
    too_many["source"]["files"] = [{"path": f"pages/f{i}.py", "content": "x"} for i in range(21)]
    with pytest.raises(ToolRefused, match="files"):
        heal_locator(core, too_many)
    with pytest.raises(ToolRefused):
        heal_locator(core, {**advise_body(), "options": "advise"})
    with pytest.raises(ToolRefused):
        heal_locator(core, [1, 2])                                # type: ignore[arg-type]
    assert core.store.count_rows("heals") == 0


def test_audit_locators_returns_the_ranked_locators_and_validates_like_the_api():
    core = session(FakeModel())
    out = audit_locators(core, audit_body())
    assert out["summary"]["locators"] == 9 and out["summary"]["upgradeable"] == 5
    with pytest.raises(ToolRefused, match="schema"):
        audit_locators(core, {"source": {"files": []}})
    with pytest.raises(ToolRefused, match="cookies"):
        audit_locators(core, {**audit_body(), "cookies": []})


def test_a_missing_mcp_package_is_reported_with_the_install_instruction(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "mcp", None)               # `import mcp` raises ImportError
    assert cli.main(["mcp"]) == 2
    assert 'pip install "voyageur[mcp]"' in capsys.readouterr().err


def test_the_server_lists_both_tools_and_answers_over_the_sdk():
    pytest.importorskip("mcp")
    from voyageur.mcp_server import build_server
    server = build_server(session(FakeModel()))

    async def go():
        tools = await server.list_tools()
        out = await server.call_tool("heal_locator", {"request": advise_body()})
        bad = None
        try:
            await server.call_tool("heal_locator", {"request": {"client": {}}})
        except Exception as e:                                  # the SDK's tool error for a refused request
            bad = e
        return tools, out, bad
    tools, out, bad = asyncio.run(go())
    assert {t.name for t in tools} == {"heal_locator", "audit_locators", "overlay_locators"}
    assert all(t.description and (getattr(t, "input_schema", None) or t.inputSchema)["properties"]["request"] for t in tools)
    assert "advised" in json.dumps(out.model_dump(mode="json"), default=str)
    assert bad is not None and "schema" in str(bad)


def test_over_stdio_a_client_lists_the_tools_and_calls_both(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=["-m", "voyageur.cli", "mcp"],
                                   env={**{k: v for k, v in os.environ.items() if k.startswith(("PATH", "HOME", "LANG"))},
                                        "PYTHONPATH": SRC, "HEAL_DB": str(tmp_path / "mcp.db"), "HEAL_WORKERS": "0"})

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session_:
                await session_.initialize()
                tools = await session_.list_tools()
                healed = await session_.call_tool("heal_locator", {"request": advise_body()})
                audited = await session_.call_tool("audit_locators", {"request": audit_body()})
                refused = await session_.call_tool("heal_locator", {"request": {"cookies": []}})
                overlaid = await session_.call_tool("overlay_locators", {"request": overlay_body(["self.page.locator('#user')"])})
                return tools, healed, audited, refused, overlaid
    tools, healed, audited, refused, overlaid = asyncio.run(asyncio.wait_for(go(), 60))
    assert not overlaid.is_error and overlaid.content[0].type == "image" and overlaid.content[0].mime_type == "image/png"
    assert json.loads(overlaid.content[1].text)["legend"][0]["status"] == "matched"
    assert sorted(t.name for t in tools.tools) == ["audit_locators", "heal_locator", "overlay_locators"]
    assert not healed.is_error and json.loads(healed.content[0].text)["status"] == "advised"
    assert not audited.is_error and json.loads(audited.content[0].text)["summary"]["upgradeable"] == 5
    assert refused.is_error and "cookies" in refused.content[0].text


def test_the_mcp_extra_is_pinned_to_the_tested_major_and_no_fallback_remains():
    root = Path(__file__).resolve().parents[2]
    assert '"mcp>=2.3,<3"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    code = (root / "src" / "voyageur" / "mcp_server.py").read_text(encoding="utf-8")
    assert "fastmcp" not in code.lower() and "mcp.server.mcpserver" in code


# ---- diagnose_failure (listed only when the service has diagnose on) ---------------------------------------------------
def diagnose_body(**over):
    from tests.unit.test_diagnose import drequest
    return drequest(**over)


def test_diagnose_failure_returns_the_diagnosis_and_validates_like_the_api():
    from tests.unit.test_diagnose import reply
    from voyageur.mcp_server import diagnose_failure
    core = session(FakeModel(reply()), diagnose_enabled=True)
    out = diagnose_failure(core, diagnose_body())
    assert out["status"] == "diagnosed" and out["diagnosis"]["cause"] == "stale_expectation" and out["usage"]["calls"] == 1
    assert core.get(out["heal_id"])["mode"] == "diagnose"
    typed = diagnose_body()
    typed["page"]["inventory"][0]["value"] = "hunter2"
    with pytest.raises(ToolRefused) as e:
        diagnose_failure(core, typed)
    assert "hunter2" not in str(e.value) and "value" in str(e.value)
    with pytest.raises(ToolRefused, match="schema"):
        diagnose_failure(core, diagnose_body(prior_heals=[{"heal_id": "x"}]))
    with pytest.raises(ToolRefused):
        diagnose_failure(core, [1, 2])                            # type: ignore[arg-type]


def test_diagnose_failure_with_diagnose_off_returns_the_refusal_as_a_result_and_calls_no_model():
    from voyageur.mcp_server import diagnose_failure
    model = FakeModel()
    out = diagnose_failure(session(model), diagnose_body())
    assert out["status"] == "needs_human" and out["reason"] == "diagnose_off" and model.calls == []


def test_the_server_lists_diagnose_failure_only_when_diagnose_is_on():
    pytest.importorskip("mcp")
    from voyageur.mcp_server import build_server

    async def names(core):
        return {t.name for t in await build_server(core).list_tools()}
    assert asyncio.run(names(session(FakeModel()))) == {"heal_locator", "audit_locators", "overlay_locators"}
    assert asyncio.run(names(session(FakeModel(), diagnose_enabled=True))) == {"heal_locator", "audit_locators",
                                                                              "overlay_locators", "diagnose_failure"}
