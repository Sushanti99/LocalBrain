import asyncio

import brain.session as session_module
from brain.session import SessionManager


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
