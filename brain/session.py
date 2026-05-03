"""In-memory single-session lifecycle management."""

from __future__ import annotations

import asyncio
from datetime import date

from brain.models import AgentName, SessionState, Turn
from brain.utils import utc_now


class SessionManager:
    def __init__(self, agent_name: AgentName):
        self.agent_name = agent_name
        self._session: SessionState | None = None
        self._session_counter = 0
        self._websocket = None
        self._run_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def current_agent(self) -> AgentName:
        return self.agent_name

    def get_or_create_session(self) -> SessionState:
        if self._session is None:
            self._session_counter += 1
            session_id = f"{date.today().isoformat()}-session-{self._session_counter}"
            self._session = SessionState(
                session_id=session_id,
                started_at=utc_now(),
                agent_name=self.agent_name,
                lifecycle_state="connected",
            )
        return self._session

    def current_session(self) -> SessionState | None:
        return self._session

    async def attach_websocket(self, websocket) -> SessionState:
        async with self._lock:
            if self._websocket is not None:
                raise RuntimeError("An active browser session is already connected.")
            self._websocket = websocket
            session = self.get_or_create_session()
            session.websocket_connected = True
            if session.lifecycle_state == "idle":
                session.lifecycle_state = "connected"
            return session

    async def detach_websocket(self, websocket) -> None:
        async with self._lock:
            if self._websocket is websocket:
                self._websocket = None
            if self._session is not None:
                self._session.websocket_connected = False
                if self._session.lifecycle_state != "closed" and not self._session.running:
                    self._session.lifecycle_state = "idle"

    def add_turn(self, role: str, content: str, *, agent_name: AgentName | None = None) -> None:
        session = self.get_or_create_session()
        session.history.append(Turn(role=role, content=content, created_at=utc_now(), agent_name=agent_name))

    def mark_running(self, task: asyncio.Task) -> None:
        session = self.get_or_create_session()
        self._run_task = task
        session.running = True
        session.lifecycle_state = "running"

    def finish_run(self, assistant_content: str, modified_files: set[str], *, agent_name: AgentName | None = None) -> None:
        session = self.get_or_create_session()
        if assistant_content:
            session.history.append(Turn(role="assistant", content=assistant_content, created_at=utc_now(), agent_name=agent_name))
        session.modified_files.update(modified_files)
        session.running = False
        session.lifecycle_state = "connected" if session.websocket_connected else "idle"
        self._run_task = None

    def fail_run(self) -> None:
        session = self.get_or_create_session()
        session.running = False
        session.lifecycle_state = "connected" if session.websocket_connected else "idle"
        self._run_task = None

    async def cancel_run(self) -> None:
        if self._run_task is None:
            return
        self._run_task.cancel()
        try:
            await self._run_task
        except asyncio.CancelledError:
            pass
        finally:
            self._run_task = None
            if self._session is not None:
                self._session.running = False

    def mark_summarizing(self) -> SessionState:
        session = self.get_or_create_session()
        session.lifecycle_state = "summarizing"
        return session

    async def switch_agent(self, agent_name: AgentName) -> SessionState:
        async with self._lock:
            session = self.get_or_create_session()
            if session.running:
                raise RuntimeError("Cannot switch agents while a run is in progress.")
            self.agent_name = agent_name
            session.agent_name = agent_name
            return session

    def close_session(self) -> SessionState | None:
        session = self._session
        if session is not None:
            session.lifecycle_state = "closed"
            session.running = False
            session.websocket_connected = False
        self._websocket = None
        self._run_task = None
        self._session = None
        return session
