"""FastAPI server and websocket bridge."""

from __future__ import annotations

import asyncio
import os
import socket
import webbrowser
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from threading import Timer

import uvicorn
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import re

from brain.agents import available_agents
from brain import integrations_api, mcp_config
from brain.agent_backends import get_backend
from brain.daily import generate_daily_note
from brain.env_config import integration_status
from brain.integration_context import fetch_tagged_integration_data
from brain.models import AgentName, AppConfig, EnvConfig, SessionState
from brain.prompts import build_chat_prompt, build_codex_prompt
from brain.session import SessionManager
from brain.summarizer import build_summary_prompt, fallback_summary, write_session_summary
from brain.vault import diff_modified_files, read_daily_note, resolve_vault_paths, snapshot_vault_mtimes


@dataclass(slots=True)
class AppRuntime:
    app_cfg: AppConfig
    env_cfg: EnvConfig
    session_manager: SessionManager

    @property
    def active_agent(self) -> AgentName:
        return self.session_manager.current_agent()

    @property
    def configured_agent(self) -> AgentName:
        return self.app_cfg.agent

    def available_agents_payload(self) -> list[dict[str, str | bool | None]]:
        payload: list[dict[str, str | bool | None]] = []
        for option in available_agents(self.app_cfg):
            backend = get_backend(self.app_cfg, option.id)
            validation = backend.validate_installation()
            payload.append(
                {
                    "id": option.id,
                    "label": option.label,
                    "installed": validation.installed,
                    "command": validation.command,
                    "version": validation.version,
                    "error": validation.error,
                }
            )
        return payload

    def installed_agent_ids(self) -> set[str]:
        return {str(option["id"]) for option in self.available_agents_payload() if option.get("installed")}


def create_app(runtime: AppRuntime) -> FastAPI:
    app = FastAPI()
    app.state.runtime = runtime

    # Headers to prevent browser caching during development
    NO_CACHE_HEADERS = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    @app.get("/")
    async def index():
        return FileResponse(
            Path(__file__).parent / "web" / "index.html",
            headers=NO_CACHE_HEADERS,
        )

    @app.get("/favicon.ico")
    async def favicon():
        return FileResponse(
            Path(__file__).parent.parent / "brainsquared-favicon.ico",
            media_type="image/x-icon",
            headers=NO_CACHE_HEADERS,
        )

    @app.get("/wordmark.svg")
    async def wordmark():
        return FileResponse(
            Path(__file__).parent / "web" / "wordmark.svg",
            media_type="image/svg+xml",
            headers=NO_CACHE_HEADERS,
        )

    @app.get("/api/status")
    async def get_status():
        backend = get_backend(runtime.app_cfg, runtime.active_agent)
        backend_status = backend.validate_installation()
        session = runtime.session_manager.current_session()
        return JSONResponse(
            {
                "vault_path": str(runtime.app_cfg.vault.path),
                "agent": runtime.active_agent,
                "configured_agent": runtime.configured_agent,
                "active_agent": runtime.active_agent,
                "available_agents": runtime.available_agents_payload(),
                "backend": {
                    "installed": backend_status.installed,
                    "command": backend_status.command,
                    "resolved_path": backend_status.resolved_path,
                    "version": backend_status.version,
                    "error": backend_status.error,
                },
                "server": {
                    "host": runtime.app_cfg.server.host,
                    "port": runtime.app_cfg.server.port,
                },
                "folders": {
                    "daily": runtime.app_cfg.vault.daily_folder,
                    "core": runtime.app_cfg.vault.core_folder,
                    "references": runtime.app_cfg.vault.references_folder,
                    "thoughts": runtime.app_cfg.vault.thoughts_folder,
                    "system": runtime.app_cfg.vault.system_folder,
                },
                "integrations": integration_status(runtime.env_cfg),
                "session": {
                    "session_id": session.session_id if session else None,
                    "state": session.lifecycle_state if session else "idle",
                    "running": session.running if session else False,
                },
            }
        )

    @app.post("/api/daily")
    async def post_daily(force: bool = Query(default=False), integrations: str = Query(default="")):
        try:
            enabled = set(integrations.split(",")) if integrations.strip() else None
            path = generate_daily_note(runtime.app_cfg, runtime.env_cfg, force=force, enabled_integrations=enabled)
            return JSONResponse({"status": "ok", "path": str(path)})
        except FileExistsError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=409)
        except Exception as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    @app.patch("/api/daily/task")
    async def patch_task(body: dict):
        text = (body.get("text") or "").strip()
        checked = bool(body.get("checked", False))
        if not text:
            return JSONResponse({"status": "error", "message": "text required"}, status_code=400)
        vault_paths = resolve_vault_paths(runtime.app_cfg)
        today_path = vault_paths.daily / f"{date.today().isoformat()}.md"
        if not today_path.exists():
            return JSONResponse({"status": "error", "message": "No daily note for today"}, status_code=404)
        raw = today_path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        for i, line in enumerate(lines):
            if text in line and ("- [ ]" in line or "- [x]" in line):
                lines[i] = line.replace("- [ ]", "- [x]") if checked else line.replace("- [x]", "- [ ]")
                break
        today_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return JSONResponse({"status": "ok"})

    @app.get("/api/daily")
    async def get_daily(offset: int = Query(default=0)):
        if offset not in {0, -1}:
            return JSONResponse(
                {"status": "error", "message": "Only offset=0 (today) and offset=-1 (yesterday) are supported."},
                status_code=400,
            )

        selected_date = date.today() + timedelta(days=offset)
        selected_date_iso = selected_date.isoformat()
        vault_paths = resolve_vault_paths(runtime.app_cfg)
        content = read_daily_note(vault_paths, selected_date_iso)
        relative_path = f"{runtime.app_cfg.vault.daily_folder}/{selected_date_iso}.md"
        label = "Today" if offset == 0 else "Yesterday"

        return JSONResponse(
            {
                "status": "ok",
                "offset": offset,
                "label": label,
                "date": selected_date_iso,
                "path": relative_path,
                "exists": content is not None,
                "content": _strip_frontmatter(content or ""),
            }
        )

    @app.get("/api/notes")
    async def get_notes():
        vault_paths = resolve_vault_paths(runtime.app_cfg)
        cfg = runtime.app_cfg.vault
        folders = [
            (cfg.core_folder, vault_paths.core),
            (cfg.references_folder, vault_paths.references),
            (cfg.thoughts_folder, vault_paths.thoughts),
            (cfg.daily_folder, vault_paths.daily),
        ]
        notes = []
        for folder_name, folder_path in folders:
            if not folder_path.exists():
                continue
            reverse = folder_name == cfg.daily_folder
            for md_file in sorted(folder_path.glob("*.md"), reverse=reverse):
                notes.append({
                    "title": md_file.stem if folder_name == cfg.daily_folder else md_file.stem.replace("-", " ").replace("_", " ").title(),
                    "path": f"{folder_name}/{md_file.name}",
                    "folder": folder_name,
                })
        return JSONResponse({"status": "ok", "notes": notes})

    @app.get("/api/notes/{note_path:path}")
    async def get_note(note_path: str):
        vault_root = runtime.app_cfg.vault.path
        full_path = (vault_root / note_path).resolve()
        if not str(full_path).startswith(str(vault_root.resolve())):
            return JSONResponse({"status": "error", "message": "Invalid path."}, status_code=400)
        if not full_path.exists() or full_path.suffix != ".md":
            return JSONResponse({"status": "error", "message": "Not found."}, status_code=404)
        return JSONResponse({"status": "ok", "path": note_path, "content": full_path.read_text(encoding="utf-8")})

    @app.post("/api/session/end")
    async def post_end_session():
        session = runtime.session_manager.current_session()
        if session is None:
            return JSONResponse({"status": "error", "message": "No active session."}, status_code=404)
        runtime.session_manager.mark_summarizing()
        await runtime.session_manager.cancel_run()
        thoughts_dir = resolve_vault_paths(runtime.app_cfg).thoughts
        backend = get_backend(runtime.app_cfg, runtime.active_agent)
        summary_text = None
        try:
            summary_prompt = build_summary_prompt(session)
            summary_text = await backend.summarize(summary_prompt, runtime.app_cfg.vault.path, _build_backend_env(runtime.env_cfg))
        except Exception:
            summary_text = fallback_summary(session)
        summary_path = await write_session_summary(thoughts_dir, session, agent_summary_text=summary_text)
        runtime.session_manager.close_session()
        return JSONResponse({"status": "ok", "path": str(summary_path)})

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        try:
            session = await runtime.session_manager.attach_websocket(websocket)
        except RuntimeError as exc:
            await websocket.send_json({"type": "session_conflict", "message": str(exc)})
            await websocket.close(code=1008)
            return

        await websocket.send_json(_session_payload(runtime, session))

        try:
            while True:
                payload = await websocket.receive_json()
                message_type = payload.get("type")
                if message_type == "ping":
                    await websocket.send_json({"type": "status", "state": "connected"})
                    continue
                if message_type == "cancel":
                    await runtime.session_manager.cancel_run()
                    await websocket.send_json({"type": "status", "state": "cancelled"})
                    continue
                if message_type == "switch_agent":
                    current_session = runtime.session_manager.get_or_create_session()
                    if current_session.running:
                        await websocket.send_json({"type": "error", "message": "Wait for the current run to finish before switching agents."})
                        continue
                    requested_agent = payload.get("agent")
                    installed_ids = runtime.installed_agent_ids()
                    if requested_agent not in installed_ids:
                        await websocket.send_json({"type": "error", "message": "Selected agent is not installed or unavailable."})
                        continue
                    session = await runtime.session_manager.switch_agent(requested_agent)
                    await websocket.send_json(_session_payload(runtime, session))
                    continue
                if message_type != "message":
                    await websocket.send_json({"type": "error", "message": "Unsupported websocket message type."})
                    continue

                current_session = runtime.session_manager.get_or_create_session()
                if current_session.running:
                    await websocket.send_json({"type": "busy", "message": "Agent run already in progress."})
                    continue

                content = (payload.get("content") or "").strip()
                if not content:
                    continue

                run_agent = runtime.active_agent
                runtime.session_manager.add_turn("user", content, agent_name=run_agent)
                run_task = asyncio.create_task(_run_backend_stream(runtime, websocket, content, run_agent))
                runtime.session_manager.mark_running(run_task)
        except WebSocketDisconnect:
            pass
        finally:
            await runtime.session_manager.detach_websocket(websocket)

    @app.post("/api/seed")
    async def post_seed():
        from brain.seeder import SeedSources, run_seed_streaming
        vault_path = runtime.app_cfg.vault.path

        async def _stream():
            async for line in run_seed_streaming(vault_path, agent=runtime.active_agent, env_cfg=runtime.env_cfg):
                yield line + "\n"

        return StreamingResponse(_stream(), media_type="text/plain")

    integrations_api.register(app, runtime)
    return app


_ACTION_TAG_RE = re.compile(r"\[action: @(\w+)\]")


async def _run_backend_stream(runtime: AppRuntime, websocket: WebSocket, user_message: str, agent_name: AgentName) -> None:
    backend = get_backend(runtime.app_cfg, agent_name)
    mcp_config.sync_from_env(agent_name)
    session = runtime.session_manager.get_or_create_session()
    vault_paths = resolve_vault_paths(runtime.app_cfg)

    tags = _ACTION_TAG_RE.findall(user_message)
    clean_message = _ACTION_TAG_RE.sub("", user_message).strip()

    live_context: str | None = None
    if tags:
        try:
            await websocket.send_json({"type": "tool_use", "tool": f"fetching {', '.join(tags)}"})
            live_context = await asyncio.get_event_loop().run_in_executor(
                None,
                fetch_tagged_integration_data,
                tags,
                clean_message,
                runtime.app_cfg,
                runtime.env_cfg,
            )
        except Exception as exc:
            live_context = f"Error fetching integration data: {exc}"

    prompt = (
        build_chat_prompt(
            runtime.app_cfg,
            session,
            clean_message,
            vault_paths,
            inject_canonical_prompt=False,
            live_integration_context=live_context,
        )
        if agent_name == "claude-code"
        else build_codex_prompt(runtime.app_cfg, session, clean_message, vault_paths, live_integration_context=live_context)
    )
    before = snapshot_vault_mtimes(runtime.app_cfg.vault.path)
    assistant_chunks: list[str] = []
    try:
        await websocket.send_json({"type": "status", "state": "thinking"})
        async for event in backend.stream(prompt, runtime.app_cfg.vault.path, _build_backend_env(runtime.env_cfg)):
            if event.type == "chunk" and event.content:
                assistant_chunks.append(event.content)
                await websocket.send_json({"type": "chunk", "content": event.content, "agent": agent_name})
            elif event.type == "todos" and event.content:
                await websocket.send_json({"type": "todos", "todos": event.content})
            elif event.type == "tool_use" and event.content:
                await websocket.send_json({"type": "tool_use", "tool": event.content})
            elif event.type == "error":
                after = snapshot_vault_mtimes(runtime.app_cfg.vault.path)
                modified_files = diff_modified_files(before, after)
                if assistant_chunks or modified_files:
                    runtime.session_manager.finish_run("".join(assistant_chunks), modified_files, agent_name=agent_name)
                else:
                    runtime.session_manager.fail_run()
                await websocket.send_json({"type": "error", "message": event.content or "Backend error"})
                return
        after = snapshot_vault_mtimes(runtime.app_cfg.vault.path)
        runtime.session_manager.finish_run("".join(assistant_chunks), diff_modified_files(before, after), agent_name=agent_name)
        await websocket.send_json({"type": "done", "content": "".join(assistant_chunks), "agent": agent_name})
    except asyncio.CancelledError:
        after = snapshot_vault_mtimes(runtime.app_cfg.vault.path)
        modified_files = diff_modified_files(before, after)
        if assistant_chunks or modified_files:
            runtime.session_manager.finish_run("".join(assistant_chunks), modified_files, agent_name=agent_name)
        else:
            runtime.session_manager.fail_run()
        await websocket.send_json({"type": "error", "message": "Agent run cancelled."})
        raise
    except Exception as exc:
        after = snapshot_vault_mtimes(runtime.app_cfg.vault.path)
        modified_files = diff_modified_files(before, after)
        if assistant_chunks or modified_files:
            runtime.session_manager.finish_run("".join(assistant_chunks), modified_files, agent_name=agent_name)
        else:
            runtime.session_manager.fail_run()
        await websocket.send_json({"type": "error", "message": str(exc)})


def _strip_frontmatter(content: str) -> str:
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            return content[end + 4:].lstrip("\n")
    return content


def _session_payload(runtime: AppRuntime, session: SessionState) -> dict[str, object]:
    return {
        "type": "session",
        "session_id": session.session_id,
        "agent": runtime.active_agent,
        "available_agents": runtime.available_agents_payload(),
    }


def run_server(app_cfg: AppConfig, env_cfg: EnvConfig, *, open_browser: bool = True) -> None:
    selected_port = resolve_server_port(app_cfg.server.host, app_cfg.server.port)
    if selected_port != app_cfg.server.port:
        print(
            f"Port {app_cfg.server.port} is already in use on {app_cfg.server.host}. "
            f"Starting Brain on port {selected_port} instead."
        )
        app_cfg.server.port = selected_port

    runtime = AppRuntime(
        app_cfg=app_cfg,
        env_cfg=env_cfg,
        session_manager=SessionManager(app_cfg.agent),
    )
    app = create_app(runtime)
    if open_browser and app_cfg.server.auto_open_browser:
        Timer(1.0, lambda: webbrowser.open(f"http://{app_cfg.server.host}:{app_cfg.server.port}")).start()
    uvicorn.run(app, host=app_cfg.server.host, port=app_cfg.server.port)


def resolve_server_port(host: str, preferred_port: int, *, max_port_tries: int = 20) -> int:
    for port in range(preferred_port, min(preferred_port + max_port_tries, 65536)):
        if port_is_available(host, port):
            return port
    raise RuntimeError(
        f"Could not find an available port on {host} starting at {preferred_port} "
        f"after trying {max_port_tries} ports."
    )


def port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _build_backend_env(env_cfg: EnvConfig) -> dict[str, str]:
    env = os.environ.copy()
    env["GOOGLE_CREDENTIALS_FILE"] = str(env_cfg.google_credentials_file)
    env["GOOGLE_TOKEN_FILE"] = str(env_cfg.google_token_file)
    env["NOTION_API_KEY"] = env_cfg.notion_api_key
    env["NEWS_FEEDS"] = ",".join(env_cfg.news_feeds)
    return env
