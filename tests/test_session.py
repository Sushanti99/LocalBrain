import asyncio
from datetime import datetime

import brain.session as session_module
from brain.models import SessionState, Turn
from brain.session import SessionManager
from brain.summarizer import session_agent_label, write_session_summary


def test_session_manager_lifecycle():
    class FakeDate:
        @staticmethod
        def today():
            return type("FakeDay", (), {"isoformat": lambda self: "2026-04-11"})()

    original_date = session_module.date
    session_module.date = FakeDate
    manager = SessionManager("claude-code")

    async def run():
        websocket = object()
        session = await manager.attach_websocket(websocket)
        assert session.websocket_connected is True
        manager.add_turn("user", "hello")
        task = asyncio.create_task(asyncio.sleep(0))
        manager.mark_running(task)
        await asyncio.sleep(0)
        manager.finish_run("world", {"daily/2026-04-11.md"})
        assert manager.current_session().history[-1].content == "world"
        assert "daily/2026-04-11.md" in manager.current_session().modified_files
        await manager.detach_websocket(websocket)
        closed = manager.close_session()
        assert closed.session_id == "2026-04-11-session-1"
        assert manager.current_session() is None

    try:
        asyncio.run(run())
    finally:
        session_module.date = original_date


def test_session_manager_switch_agent_preserves_session():
    manager = SessionManager("claude-code")

    async def run():
        websocket = object()
        session = await manager.attach_websocket(websocket)
        original_session_id = session.session_id
        switched = await manager.switch_agent("codex")
        assert switched.session_id == original_session_id
        assert switched.agent_name == "codex"
        assert manager.current_agent() == "codex"
        manager.add_turn("user", "hello again", agent_name="codex")
        manager.finish_run("done", set(), agent_name="codex")
        assert manager.current_session().history[-1].agent_name == "codex"

    asyncio.run(run())


def test_session_agent_label_returns_mixed_for_multi_agent_history():
    session = SessionState(
        session_id="2026-04-11-session-1",
        started_at=datetime.now().astimezone(),
        agent_name="codex",
        history=[
            Turn(role="user", content="First", created_at=datetime.now().astimezone(), agent_name="claude-code"),
            Turn(role="assistant", content="Reply", created_at=datetime.now().astimezone(), agent_name="codex"),
        ],
    )

    assert session_agent_label(session) == "mixed"


def test_write_session_summary_marks_mixed_agent(tmp_path):
    session = SessionState(
        session_id="2026-04-11-session-1",
        started_at=datetime.now().astimezone(),
        agent_name="codex",
        history=[
            Turn(role="user", content="Ask Claude", created_at=datetime.now().astimezone(), agent_name="claude-code"),
            Turn(role="assistant", content="Answer", created_at=datetime.now().astimezone(), agent_name="codex"),
        ],
    )

    output_path = asyncio.run(write_session_summary(tmp_path, session, agent_summary_text="# Session Summary"))

    assert "agent: mixed" in output_path.read_text(encoding="utf-8")
