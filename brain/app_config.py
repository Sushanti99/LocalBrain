"""Application config loading and writing."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from brain.agents import SUPPORTED_AGENTS
from brain.models import (
    AgentCommandConfig,
    AppConfig,
    IntegrationsConfig,
    ServerConfig,
    SessionConfig,
    VaultConfig,
)
from brain.utils import ensure_absolute_path


def _build_default_config(vault_path: Path, agent: str = "claude-code") -> dict[str, Any]:
    return {
        "agent": agent,
        "server": {
            "host": "127.0.0.1",
            "port": 3000,
            "auto_open_browser": True,
        },
        "vault": {
            "path": str(vault_path),
            "daily_folder": "daily",
            "core_folder": "core",
            "references_folder": "references",
            "thoughts_folder": "thoughts",
            "system_folder": "system",
        },
        "session": {
            "single_session": True,
            "history_turn_limit": 10,
            "summarize_on_end": True,
            "auto_save_summary": True,
            "inactivity_timeout_seconds": 120,
        },
        "agents": {
            "claude-code": {
                "command": "claude",
                "args": ["-p", "--output-format", "stream-json", "--verbose"],
                "allowed_tools": ["Read", "Edit", "Bash", "Glob", "Grep"],
            },
            "codex": {
                "command": "codex",
                "args": ["exec", "--json", "--sandbox", "workspace-write"],
            },
        },
        "integrations": {
            "enable_daily_context": True,
            "include_in_prompt": False,
        },
    }


def default_app_config(vault_path: Path, agent: str = "claude-code") -> AppConfig:
    data = _build_default_config(vault_path, agent)
    return _parse_app_config(data, None)


def load_app_config(
    *,
    vault_path: str | Path | None = None,
    config_path: str | Path | None = None,
    agent_override: str | None = None,
    port_override: int | None = None,
) -> AppConfig:
    resolved_config_path: Path | None = None

    if config_path is not None:
        resolved_config_path = ensure_absolute_path(config_path)
    elif vault_path is not None:
        resolved_config_path = ensure_absolute_path(vault_path) / "system" / "brain.config.yaml"

    if resolved_config_path is None or not resolved_config_path.exists():
        raise FileNotFoundError("Could not find brain.config.yaml. Supply --config or initialize the vault first.")

    data = yaml.safe_load(resolved_config_path.read_text(encoding="utf-8")) or {}
    app_cfg = _parse_app_config(data, resolved_config_path)

    if agent_override is not None:
        if agent_override not in SUPPORTED_AGENTS:
            raise ValueError(f"Unsupported agent '{agent_override}'. Expected one of: {', '.join(sorted(SUPPORTED_AGENTS))}.")
        app_cfg.agent = agent_override

    if port_override is not None:
        app_cfg.server.port = port_override

    validate_app_config(app_cfg, allow_missing_vault=False)
    return app_cfg


def validate_app_config(app_cfg: AppConfig, *, allow_missing_vault: bool) -> None:
    if app_cfg.agent not in SUPPORTED_AGENTS:
        raise ValueError(f"Unsupported agent '{app_cfg.agent}'.")
    if not allow_missing_vault and not app_cfg.vault.path.exists():
        raise FileNotFoundError(f"Vault path does not exist: {app_cfg.vault.path}")
    if not (1 <= app_cfg.server.port <= 65535):
        raise ValueError(f"Server port must be between 1 and 65535, got {app_cfg.server.port}.")
    if app_cfg.session.history_turn_limit <= 0:
        raise ValueError("session.history_turn_limit must be positive.")


def write_default_app_config(
    config_path: Path,
    vault_path: Path,
    *,
    agent: str,
    folder_overrides: dict[str, str] | None = None,
    overwrite: bool = False,
) -> Path:
    if config_path.exists() and not overwrite:
        return config_path

    data = _build_default_config(vault_path, agent)
    if folder_overrides:
        data["vault"].update(folder_overrides)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return config_path


def _parse_app_config(data: dict[str, Any], config_path: Path | None) -> AppConfig:
    if "vault" not in data:
        raise ValueError("brain.config.yaml is missing required 'vault' section.")
    vault_data = data["vault"]
    if "path" not in vault_data:
        raise ValueError("brain.config.yaml is missing vault.path.")

    vault_path = ensure_absolute_path(vault_data["path"])
    server_data = data.get("server", {})
    session_data = data.get("session", {})
    integrations_data = data.get("integrations", {})
    agents_data = data.get("agents", {})

    agents: dict[str, AgentCommandConfig] = {}
    for name, agent_data in agents_data.items():
        agents[name] = AgentCommandConfig(
            command=agent_data["command"],
            args=list(agent_data.get("args", [])),
            allowed_tools=list(agent_data.get("allowed_tools", [])),
        )

    if not agents:
        defaults = default_app_config(vault_path)
        agents = defaults.agents

    app_cfg = AppConfig(
        agent=data.get("agent", "claude-code"),
        server=ServerConfig(
            host=server_data.get("host", "127.0.0.1"),
            port=int(server_data.get("port", 3000)),
            auto_open_browser=bool(server_data.get("auto_open_browser", True)),
        ),
        vault=VaultConfig(
            path=vault_path,
            daily_folder=vault_data.get("daily_folder", "daily"),
            core_folder=vault_data.get("core_folder", "core"),
            references_folder=vault_data.get("references_folder", "references"),
            thoughts_folder=vault_data.get("thoughts_folder", "thoughts"),
            system_folder=vault_data.get("system_folder", "system"),
        ),
        session=SessionConfig(
            single_session=bool(session_data.get("single_session", True)),
            history_turn_limit=int(session_data.get("history_turn_limit", 10)),
            summarize_on_end=bool(session_data.get("summarize_on_end", True)),
            auto_save_summary=bool(session_data.get("auto_save_summary", True)),
            inactivity_timeout_seconds=int(session_data.get("inactivity_timeout_seconds", 120)),
        ),
        agents=agents,
        integrations=IntegrationsConfig(
            enable_daily_context=bool(integrations_data.get("enable_daily_context", True)),
            include_in_prompt=bool(integrations_data.get("include_in_prompt", False)),
        ),
        config_path=config_path,
    )
    return app_cfg


def app_config_to_dict(app_cfg: AppConfig) -> dict[str, Any]:
    data = asdict(app_cfg)
    data["vault"]["path"] = str(app_cfg.vault.path)
    if app_cfg.config_path is not None:
        data["config_path"] = str(app_cfg.config_path)
    return data
