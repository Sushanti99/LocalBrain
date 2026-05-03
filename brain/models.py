"""Shared models for Brain V1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


AgentName = Literal["claude-code", "codex"]
SessionLifecycleState = Literal["idle", "connected", "running", "summarizing", "closed"]
BackendEventType = Literal["status", "chunk", "done", "error", "todos", "tool_use"]
TurnRole = Literal["user", "assistant"]


@dataclass(slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 3000
    auto_open_browser: bool = True


@dataclass(slots=True)
class VaultConfig:
    path: Path
    daily_folder: str = "daily"
    core_folder: str = "core"
    references_folder: str = "references"
    thoughts_folder: str = "thoughts"
    system_folder: str = "system"


@dataclass(slots=True)
class SessionConfig:
    single_session: bool = True
    history_turn_limit: int = 10
    summarize_on_end: bool = True
    auto_save_summary: bool = True
    inactivity_timeout_seconds: int = 120


@dataclass(slots=True)
class AgentCommandConfig:
    command: str
    args: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IntegrationsConfig:
    enable_daily_context: bool = True
    include_in_prompt: bool = False


@dataclass(slots=True)
class AppConfig:
    agent: AgentName
    server: ServerConfig
    vault: VaultConfig
    session: SessionConfig
    agents: dict[str, AgentCommandConfig]
    integrations: IntegrationsConfig
    config_path: Path | None = None


@dataclass(slots=True)
class EnvConfig:
    google_credentials_file: Path
    google_token_file: Path
    notion_api_key: str
    news_feeds: list[str]
    raw_env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class VaultPaths:
    root: Path
    daily: Path
    core: Path
    references: Path
    thoughts: Path
    system: Path


@dataclass(slots=True)
class ObsidianNote:
    path: Path
    relative_path: str
    title: str
    content: str
    raw_content: str
    frontmatter: dict[str, Any]
    tags: list[str]
    links: list[str]
    tasks: list[dict[str, Any]]
    folder: str


@dataclass(slots=True)
class DailyContext:
    vault_notes: list[ObsidianNote] = field(default_factory=list)
    calendar_events: list[dict[str, Any]] = field(default_factory=list)
    email_items: list[dict[str, Any]] = field(default_factory=list)
    notion_tasks: list[dict[str, Any]] = field(default_factory=list)
    github_items: list[dict[str, Any]] = field(default_factory=list)
    slack_items: list[dict[str, Any]] = field(default_factory=list)
    reading_list: list[dict[str, Any]] = field(default_factory=list)
    carry_forward: list[dict[str, Any]] = field(default_factory=list)
    today: str = ""


@dataclass(slots=True)
class Turn:
    role: TurnRole
    content: str
    created_at: datetime
    agent_name: AgentName | None = None


@dataclass(slots=True)
class SessionState:
    session_id: str
    started_at: datetime
    agent_name: AgentName
    history: list[Turn] = field(default_factory=list)
    lifecycle_state: SessionLifecycleState = "idle"
    running: bool = False
    websocket_connected: bool = False
    modified_files: set[str] = field(default_factory=set)


@dataclass(slots=True)
class BackendEvent:
    type: BackendEventType
    content: str | None = None
    raw: dict[str, Any] | str | None = None


@dataclass(slots=True)
class BackendValidationResult:
    installed: bool
    command: str
    resolved_path: str | None
    version: str | None
    error: str | None = None
