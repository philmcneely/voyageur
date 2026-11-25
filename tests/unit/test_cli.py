"""The command line: an offline heal from a saved request, and the bind rules of `serve`."""
import json

import pytest

from voyageur import cli
from tests.unit.scenarios import request

pytestmark = pytest.mark.unit


def test_heal_prints_the_response_json_for_a_saved_request(tmp_path, capsys):
    path = tmp_path / "req.json"
    path.write_text(json.dumps(request(failure={"error": "AssertionError: 1 == 2", "stack": []})))
    assert cli.main(["heal", "--request", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "not_drift" and out["heal_id"].startswith("h_") and out["usage"]["calls"] == 0


def test_heal_without_a_model_says_needs_human_not_a_crash(tmp_path, capsys, monkeypatch):
    for var in ("HEAL_BASE_URL", "HEAL_MODEL", "HEAL_TEXT_BASE_URL", "HEAL_TEXT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "req.json"
    path.write_text(json.dumps(request()))
    assert cli.main(["heal", "--request", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "model_not_configured"


@pytest.mark.parametrize("body, code", [("{nope", 2), (json.dumps({"x": 1}), 2)])
def test_a_bad_request_file_is_exit_2_with_a_message_not_a_traceback(tmp_path, capsys, body, code):
    path = tmp_path / "req.json"
    path.write_text(body)
    assert cli.main(["heal", "--request", str(path)]) == code
    assert "Traceback" not in capsys.readouterr().err


def test_a_missing_request_file_is_exit_2(tmp_path):
    assert cli.main(["heal", "--request", str(tmp_path / "none.json")]) == 2


def test_a_value_in_the_request_file_is_refused_offline_too(tmp_path, capsys):
    body = request()
    body["page"]["inventory"][0]["value"] = "x"
    path = tmp_path / "req.json"
    path.write_text(json.dumps(body))
    assert cli.main(["heal", "--request", str(path)]) == 2 and "value" in capsys.readouterr().err


LONG = "k" * 32


@pytest.mark.parametrize("host, allow, token, ok", [
    ("127.0.0.1", False, None, True), ("localhost", False, None, True), ("::1", False, None, True),
    ("0.0.0.0", False, LONG, False), ("0.0.0.0", True, None, False), ("0.0.0.0", True, LONG, True),
    ("192.0.2.5", True, LONG, True), ("192.0.2.5", False, LONG, False),
])
def test_a_wider_bind_needs_the_flag_and_a_token(host, allow, token, ok):
    assert (cli.bind_problem(host, allow, token) is None) is ok


@pytest.mark.parametrize("token", ["t", "a" * 31, "secret-password"])
def test_a_wider_bind_refuses_a_short_token_and_does_not_echo_it(token):
    why = cli.bind_problem("0.0.0.0", True, token)
    assert why is not None and "32" in why and (len(token) < 3 or token not in why)


def test_serve_refuses_a_wide_bind_before_starting(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HEAL_DB", str(tmp_path / "db" / "heal.db"))
    started = []
    monkeypatch.setattr(cli, "_run_server", lambda *a, **k: started.append(a))
    monkeypatch.delenv("HEAL_SERVICE_TOKEN", raising=False)
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 2 and started == []
    monkeypatch.setenv("HEAL_SERVICE_TOKEN", "t")
    assert cli.main(["serve", "--host", "0.0.0.0", "--allow-remote"]) == 2 and not started
    monkeypatch.setenv("HEAL_SERVICE_TOKEN", LONG)
    assert cli.main(["serve", "--host", "0.0.0.0", "--allow-remote"]) == 0 and len(started) == 1
    assert cli.main(["serve"]) == 0 and started[-1][1] == "127.0.0.1"


def test_the_console_script_is_declared():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
    assert 'voyageur = "voyageur.cli:main"' in text


def test_the_server_holds_idle_connections_longer_than_clients_do(monkeypatch):
    # A client reuses a kept-alive socket after probing the page and re-running a test (Node's fetch keeps one 4 s,
    # uvicorn's own default closes it at 5 s): a server that closes first resets the client's next call (ECONNRESET).
    import uvicorn
    from voyageur import app
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda _app, **kw: seen.update(kw))
    monkeypatch.setattr(app, "create_app", lambda core, bind_host=None: object())
    cli._run_server(None, "127.0.0.1", 0)
    assert seen.get("timeout_keep_alive", 5) >= 60
