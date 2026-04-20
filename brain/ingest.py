"""Incremental vault ingest — triggered automatically when an integration connects."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from brain.app_config import default_app_config
from brain.ingest_prompts import build_ingest_prompt
from brain.models import EnvConfig
from brain.vault import resolve_vault_paths


def _collect(integration_id: str, env_cfg: EnvConfig) -> str:
    from brain.seeder import (
        _collect_github_context,
        _collect_linear_context,
        _collect_slack_context,
        collect_calendar_context,
        collect_gmail_context,
        collect_notion_context,
    )

    if integration_id == "notion":
        return collect_notion_context(env_cfg)
    if integration_id == "github":
        return _collect_github_context(os.getenv("GITHUB_TOKEN", ""))
    if integration_id == "slack":
        return _collect_slack_context(os.getenv("SLACK_BOT_TOKEN", ""))
    if integration_id == "linear":
        return _collect_linear_context(os.getenv("LINEAR_API_KEY", ""))
    if integration_id == "gmail":
        return collect_gmail_context(env_cfg)
    if integration_id == "calendar":
        return collect_calendar_context(env_cfg)
    return ""


async def _run_agent(vault_paths, prompt: str) -> None:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return

    cmd = [
        claude_bin, "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep",
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(vault_paths.root),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.communicate(prompt.encode()), timeout=120)
    except asyncio.TimeoutError:
        process.kill()


async def run_ingest(vault_path: Path, agent: str, integration_id: str, env_cfg: EnvConfig) -> None:
    """Collect a fresh snapshot from one integration and quietly update the vault."""
    vault_path = vault_path.expanduser().resolve()
    if not vault_path.exists():
        return

    vault_paths = resolve_vault_paths(default_app_config(vault_path, agent))

    data = _collect(integration_id, env_cfg)
    if not data:
        return

    ingest_file = vault_paths.system / f"_ingest_{integration_id}.md"
    ingest_file.parent.mkdir(parents=True, exist_ok=True)
    ingest_file.write_text(data, encoding="utf-8")

    try:
        prompt = build_ingest_prompt(vault_paths, integration_id)
        await _run_agent(vault_paths, prompt)
    except Exception:
        pass
    finally:
        if ingest_file.exists():
            ingest_file.unlink()
