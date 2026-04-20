from datetime import datetime

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from brain import mcp_config
from brain.app_config import default_app_config
from brain.env_config import load_env_config
from brain.models import BackendValidationResult
from brain.server import AppRuntime, create_app, resolve_server_port, run_server
from brain.session import SessionManager


class FakeBackend:
    def validate_installation(self):
        return BackendValidationResult(
            installed=True,
            command="fake",
            resolved_path="/usr/bin/fake",
            version="fake 1.0",
        )

    async def stream(self, prompt, cwd, env):
        yield type("Event", (), {"type": "chunk", "content": "hello", "raw": None})()
        yield type("Event", (), {"type": "chunk", "content": " world", "raw": None})()
        yield type("Event", (), {"type": "done", "content": None, "raw": None})()

    async def summarize(self, prompt, cwd, env):
        return "# Session Summary\n\n## Topics Discussed\n- test\n\n## Decisions Made\n- done\n\n## Files Modified\n- none\n\n## Action Items\n- none"


def _fake_server_date(value: str):
    return type(
        "FakeDate",
        (),
        {
            "today": staticmethod(
                lambda: type(
                    "FakeDateValue",
                    (),
                    {
                        "isoformat": lambda self: value,
                        "__add__": lambda self, delta: type(
                            "FakeDateValue",
                            (),
                            {
                                "isoformat": lambda self: "2026-04-11" if delta.days == -1 else value,
                            },
                        )(),
                    },
                )()
            )
        },
    )


def test_status_route_and_single_websocket_enforcement(tmp_path, monkeypatch):
    app_cfg = default_app_config(tmp_path / "vault")
    env_cfg = load_env_config()
    runtime = AppRuntime(app_cfg=app_cfg, env_cfg=env_cfg, session_manager=SessionManager(app_cfg.agent))
    app = create_app(runtime)
    monkeypatch.setattr("brain.server.get_backend", lambda cfg: FakeBackend())

    client = TestClient(app)
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["agent"] == "claude-code"

    with client.websocket_connect("/ws") as ws1:
        session_payload = ws1.receive_json()
        assert session_payload["type"] == "session"

        with client.websocket_connect("/ws") as ws2:
            error_payload = ws2.receive_json()
            assert error_payload["type"] == "session_conflict"
            assert "already connected" in error_payload["message"]


def test_websocket_message_streams_response(tmp_path, monkeypatch):
    app_cfg = default_app_config(tmp_path / "vault")
    env_cfg = load_env_config()
    runtime = AppRuntime(app_cfg=app_cfg, env_cfg=env_cfg, session_manager=SessionManager(app_cfg.agent))
    app = create_app(runtime)
    monkeypatch.setattr("brain.server.get_backend", lambda cfg: FakeBackend())

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "message", "content": "Hello"})

        statuses = []
        chunks = []
        done = None
        for _ in range(4):
            payload = ws.receive_json()
            if payload["type"] == "status":
                statuses.append(payload["state"])
            elif payload["type"] == "chunk":
                chunks.append(payload["content"])
            elif payload["type"] == "done":
                done = payload

        assert "thinking" in statuses
        assert "".join(chunks) == "hello world"
        assert done["content"] == "hello world"


def test_get_daily_route_returns_today_note(tmp_path, monkeypatch):
    app_cfg = default_app_config(tmp_path / "vault")
    env_cfg = load_env_config()
    daily_dir = app_cfg.vault.path / app_cfg.vault.daily_folder
    daily_dir.mkdir(parents=True, exist_ok=True)
    (daily_dir / "2026-04-12.md").write_text("# Today", encoding="utf-8")

    runtime = AppRuntime(app_cfg=app_cfg, env_cfg=env_cfg, session_manager=SessionManager(app_cfg.agent))
    app = create_app(runtime)
    monkeypatch.setattr("brain.server.get_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr("brain.server.date", _fake_server_date("2026-04-12"))

    client = TestClient(app)
    response = client.get("/api/daily?offset=0")

    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is True
    assert payload["label"] == "Today"
    assert payload["date"] == "2026-04-12"
    assert payload["content"] == "# Today"
    assert payload["path"] == "daily/2026-04-12.md"


def test_get_daily_route_returns_missing_yesterday_state(tmp_path, monkeypatch):
    app_cfg = default_app_config(tmp_path / "vault")
    env_cfg = load_env_config()
    runtime = AppRuntime(app_cfg=app_cfg, env_cfg=env_cfg, session_manager=SessionManager(app_cfg.agent))
    app = create_app(runtime)
    monkeypatch.setattr("brain.server.get_backend", lambda cfg: FakeBackend())
    monkeypatch.setattr("brain.server.date", _fake_server_date("2026-04-12"))

    client = TestClient(app)
    response = client.get("/api/daily?offset=-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is False
    assert payload["label"] == "Yesterday"
    assert payload["date"] == "2026-04-11"
    assert payload["content"] == ""
    assert payload["path"] == "daily/2026-04-11.md"


def test_get_daily_route_rejects_unsupported_offsets(tmp_path, monkeypatch):
    app_cfg = default_app_config(tmp_path / "vault")
    env_cfg = load_env_config()
    runtime = AppRuntime(app_cfg=app_cfg, env_cfg=env_cfg, session_manager=SessionManager(app_cfg.agent))
    app = create_app(runtime)
    monkeypatch.setattr("brain.server.get_backend", lambda cfg: FakeBackend())

    client = TestClient(app)
    response = client.get("/api/daily?offset=1")

    assert response.status_code == 400
    assert "offset=0" in response.json()["message"]


def test_integrations_status_uses_agent_specific_mcp_config(tmp_path, monkeypatch):
    app_cfg = default_app_config(tmp_path / "vault", "codex")
    env_cfg = load_env_config()
    runtime = AppRuntime(app_cfg=app_cfg, env_cfg=env_cfg, session_manager=SessionManager(app_cfg.agent))
    app = create_app(runtime)

    monkeypatch.setattr(mcp_config, "CLAUDE_SETTINGS", tmp_path / "claude-settings.json")
    monkeypatch.setattr(mcp_config, "CODEX_CONFIG", tmp_path / "codex-config.toml")
    for key in ("GITHUB_TOKEN", "SLACK_BOT_TOKEN", "SLACK_TEAM_ID", "NOTION_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_status")

    client = TestClient(app)
    response = client.get("/api/integrations/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["linear"] is True
    assert payload["github"] is False
    assert payload["slack"] is False


def test_resolve_server_port_uses_next_available_port(monkeypatch):
    monkeypatch.setattr("brain.server.port_is_available", lambda host, port: port == 3002)

    assert resolve_server_port("127.0.0.1", 3000) == 3002


def test_run_server_updates_port_before_starting(tmp_path, monkeypatch):
    app_cfg = default_app_config(tmp_path / "vault")
    env_cfg = load_env_config()
    app_cfg.server.port = 3000

    monkeypatch.setattr("brain.server.resolve_server_port", lambda host, port: 3001)
    monkeypatch.setattr("brain.server.create_app", lambda runtime: object())

    captured = {}

    def fake_uvicorn_run(app, host, port):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("brain.server.uvicorn.run", fake_uvicorn_run)

    run_server(app_cfg, env_cfg, open_browser=False)

    assert app_cfg.server.port == 3001
    assert captured["host"] == app_cfg.server.host
    assert captured["port"] == 3001
