"""Backend adapters for Claude Code and Codex."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from brain.models import AgentName, AppConfig, BackendEvent, BackendValidationResult

NO_OUTPUT_TIMEOUT_SECONDS = 120


class BaseBackend:
    name = ""

    def __init__(self, app_cfg: AppConfig):
        self.app_cfg = app_cfg
        self._process: asyncio.subprocess.Process | None = None

    @property
    def command_config(self):
        return self.app_cfg.agents[self.name]

    def validate_installation(self) -> BackendValidationResult:
        command = self.command_config.command
        resolved_path = shutil.which(command)
        if resolved_path is None:
            return BackendValidationResult(
                installed=False,
                command=command,
                resolved_path=None,
                version=None,
                error=f"Command not found on PATH: {command}",
            )
        return BackendValidationResult(
            installed=True,
            command=command,
            resolved_path=resolved_path,
            version=self.version(),
        )

    def version(self) -> str | None:
        command = self.command_config.command
        resolved_path = shutil.which(command)
        if resolved_path is None:
            return None
        for flag in ("--version", "version"):
            try:
                import subprocess

                completed = subprocess.run(
                    [resolved_path, flag],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                output = (completed.stdout or completed.stderr).strip()
                if output:
                    return output.splitlines()[0]
            except Exception:
                continue
        return None

    async def summarize(self, prompt: str, cwd: Path, env: dict[str, str]) -> str:
        chunks: list[str] = []
        async for event in self.stream(prompt, cwd, env):
            if event.type == "chunk" and event.content:
                chunks.append(event.content)
        return "".join(chunks).strip()

    async def cancel(self) -> None:
        if self._process is not None and self._process.returncode is None:
            self._process.kill()
            await self._process.wait()


class ClaudeCodeBackend(BaseBackend):
    name = "claude-code"

    def build_command(self) -> list[str]:
        config = self.command_config
        command = [config.command, *config.args]
        if config.allowed_tools:
            command.extend(["--allowedTools", *config.allowed_tools])
        return command

    async def stream(self, prompt: str, cwd: Path, env: dict[str, str]):
        command = self.build_command()
        self._process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        self._process.stdin.write(prompt.encode("utf-8"))
        await self._process.stdin.drain()
        self._process.stdin.close()
        try:
            while True:
                line = await asyncio.wait_for(self._process.stdout.readline(), timeout=NO_OUTPUT_TIMEOUT_SECONDS)
                if not line:
                    break
                event = parse_claude_stream_line(line.decode("utf-8", errors="replace"))
                if event is not None:
                    yield event
            stderr_text = await _read_stderr(self._process)
            return_code = await self._process.wait()
            if return_code != 0:
                yield BackendEvent(type="error", content=stderr_text or f"Claude exited with code {return_code}")
            else:
                yield BackendEvent(type="done")
        except asyncio.TimeoutError:
            await self.cancel()
            yield BackendEvent(type="error", content="Claude timed out waiting for output.")
        except asyncio.CancelledError:
            await self.cancel()
            raise
        finally:
            self._process = None


class CodexBackend(BaseBackend):
    name = "codex"

    def build_command(self, prompt: str, output_last_message_path: Path | None = None) -> list[str]:
        config = self.command_config
        command = [config.command, *config.args, "-C", str(self.app_cfg.vault.path)]
        if not command_has_flag(command, "--sandbox"):
            command.extend(["--sandbox", "workspace-write"])
        if not path_is_git_repo(self.app_cfg.vault.path):
            command.append("--skip-git-repo-check")
        if output_last_message_path is not None:
            command.extend(["--output-last-message", str(output_last_message_path)])
        command.append(prompt)
        return command

    async def stream(self, prompt: str, cwd: Path, env: dict[str, str]):
        output_last_message_path = Path(tempfile.mkstemp(prefix="brain-codex-", suffix=".txt")[1])
        command = self.build_command(prompt, output_last_message_path=output_last_message_path)
        self._process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._process.stdout is not None
        emitted_visible_text = False
        try:
            while True:
                line = await asyncio.wait_for(self._process.stdout.readline(), timeout=NO_OUTPUT_TIMEOUT_SECONDS)
                if not line:
                    break
                event = parse_codex_jsonl_line(line.decode("utf-8", errors="replace"))
                if event is not None:
                    if event.type == "chunk" and event.content:
                        emitted_visible_text = True
                    yield event
            stderr_text = await _read_stderr(self._process)
            return_code = await self._process.wait()
            final_text = _read_output_last_message(output_last_message_path)
            if final_text and not emitted_visible_text:
                emitted_visible_text = True
                yield BackendEvent(type="chunk", content=final_text)
            if return_code != 0:
                yield BackendEvent(type="error", content=stderr_text or f"Codex exited with code {return_code}")
            elif not emitted_visible_text:
                yield BackendEvent(
                    type="error",
                    content="Codex completed without returning visible text. This usually means the JSON event schema changed.",
                )
            else:
                yield BackendEvent(type="done", content=final_text if final_text else None)
        except asyncio.TimeoutError:
            await self.cancel()
            yield BackendEvent(type="error", content="Codex timed out waiting for output.")
        except asyncio.CancelledError:
            await self.cancel()
            raise
        finally:
            if output_last_message_path.exists():
                output_last_message_path.unlink(missing_ok=True)
            self._process = None


def parse_claude_stream_line(line: str) -> BackendEvent | None:
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return BackendEvent(type="chunk", content=line, raw=line)

    event_type = payload.get("type", "")

    # Noise — skip silently
    if event_type in {"system", "rate_limit_event", "user"}:
        return None

    # claude --print --output-format stream-json --verbose primary format
    if event_type == "assistant":
        content_blocks = payload.get("message", {}).get("content", [])
        text = "".join(b["text"] for b in content_blocks if b.get("type") == "text" and b.get("text"))
        # Emit todo list when TodoWrite is called
        for block in content_blocks:
            if block.get("type") == "tool_use" and block.get("name") == "TodoWrite":
                todos = block.get("input", {}).get("todos", [])
                if todos:
                    return BackendEvent(type="todos", content=json.dumps(todos), raw=payload)
        # Emit tool activity for non-todo tool calls
        for block in content_blocks:
            if block.get("type") == "tool_use" and block.get("name") != "TodoWrite":
                return BackendEvent(type="tool_use", content=block.get("name", "tool"), raw=payload)
        if text:
            return BackendEvent(type="chunk", content=text, raw=payload)
        return BackendEvent(type="status", content="assistant", raw=payload)

    if event_type == "result":
        if payload.get("is_error"):
            return BackendEvent(type="error", content=payload.get("result") or "Claude returned an error.", raw=payload)
        return BackendEvent(type="done", raw=payload)

    # Error fields
    if payload.get("error"):
        return BackendEvent(type="error", content=str(payload["error"]), raw=payload)

    # Legacy streaming format (content_block_delta etc.)
    if event_type in {"message_start", "content_block_start", "content_block_delta"}:
        text = payload.get("delta", {}).get("text") or payload.get("content_block", {}).get("text")
        if text:
            return BackendEvent(type="chunk", content=text, raw=payload)
        return BackendEvent(type="status", content=event_type, raw=payload)

    if event_type in {"message_delta", "message_stop"}:
        return BackendEvent(type="status", content=event_type, raw=payload)

    text = payload.get("text") or payload.get("content")
    if isinstance(text, str):
        return BackendEvent(type="chunk", content=text, raw=payload)
    return BackendEvent(type="status", content=event_type or "status", raw=payload)


def parse_codex_jsonl_line(line: str) -> BackendEvent | None:
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return BackendEvent(type="chunk", content=line, raw=line)

    event_type = payload.get("type", "")
    if event_type in {"response.output_text.delta", "text", "output_text", "agent_message.delta"}:
        text = payload.get("delta") or payload.get("text") or payload.get("content")
        if isinstance(text, str):
            return BackendEvent(type="chunk", content=text, raw=payload)
    if event_type in {"thread.started", "turn.started", "turn.completed", "item.started", "item.completed"}:
        extracted = _extract_text_candidate(payload)
        if extracted:
            return BackendEvent(type="chunk", content=extracted, raw=payload)
        return BackendEvent(type="status", content=event_type, raw=payload)
    if event_type in {"response.completed", "completed"}:
        return BackendEvent(type="done", raw=payload)
    if event_type in {"response.error", "error"}:
        message = payload.get("message") or payload.get("error") or "Codex returned an error."
        return BackendEvent(type="error", content=str(message), raw=payload)

    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return BackendEvent(type="chunk", content=content, raw=payload)
    if isinstance(payload.get("content"), str):
        return BackendEvent(type="chunk", content=payload["content"], raw=payload)
    extracted = _extract_text_candidate(payload)
    if extracted:
        return BackendEvent(type="chunk", content=extracted, raw=payload)
    return BackendEvent(type="status", content=event_type or "status", raw=payload)


def get_backend(app_cfg: AppConfig, agent_name: AgentName | None = None):
    selected_agent = agent_name or app_cfg.agent
    if selected_agent == "claude-code":
        return ClaudeCodeBackend(app_cfg)
    if selected_agent == "codex":
        return CodexBackend(app_cfg)
    raise ValueError(f"Unsupported backend: {selected_agent}")


async def _read_stderr(process: asyncio.subprocess.Process) -> str:
    if process.stderr is None:
        return ""
    data = await process.stderr.read()
    return data.decode("utf-8", errors="replace").strip()


def path_is_git_repo(path: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return completed.returncode == 0


def command_has_flag(command: list[str], flag: str) -> bool:
    return flag in command


def _read_output_last_message(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _extract_text_candidate(payload) -> str | None:
    if isinstance(payload, str):
        text = payload.strip()
        return text or None
    if isinstance(payload, list):
        for item in payload:
            extracted = _extract_text_candidate(item)
            if extracted:
                return extracted
        return None
    if not isinstance(payload, dict):
        return None

    preferred_keys = ["delta", "text", "content", "message", "output", "last_message"]
    for key in preferred_keys:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, str):
            candidate = value.strip()
            if candidate and candidate not in {payload.get("type", "")}:
                return candidate
        extracted = _extract_text_candidate(value)
        if extracted:
            return extracted
    return None
