"""Read and write Claude Code MCP server configurations (~/.claude/settings.json)."""

from __future__ import annotations

import json
from pathlib import Path

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

# MCP server definitions — command + args + which env var holds the credential
_SERVERS: dict[str, dict] = {
    "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env_map": {"api_key": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    },
    "notion": {
        "command": "npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
        "env_map": {"api_key": "NOTION_API_KEY"},
    },
    "linear": {
        "command": "npx",
        "args": ["-y", "@linear/mcp"],
        "env_map": {"api_key": "LINEAR_API_KEY"},
    },
    "slack": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env_map": {"bot_token": "SLACK_BOT_TOKEN", "team_id": "SLACK_TEAM_ID"},
    },
}


def _read() -> dict:
    if not CLAUDE_SETTINGS.exists():
        return {}
    try:
        return json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(settings: dict) -> None:
    CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_SETTINGS.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def add_server(integration_id: str, credentials: dict[str, str]) -> None:
    """Write an MCP server entry to ~/.claude/settings.json."""
    server_def = _SERVERS.get(integration_id)
    if not server_def:
        return
    env = {env_key: credentials.get(field, "") for field, env_key in server_def["env_map"].items()}
    settings = _read()
    settings.setdefault("mcpServers", {})[integration_id] = {
        "command": server_def["command"],
        "args": server_def["args"],
        "env": env,
    }
    _write(settings)


def remove_server(integration_id: str) -> None:
    """Remove an MCP server entry from ~/.claude/settings.json."""
    settings = _read()
    settings.get("mcpServers", {}).pop(integration_id, None)
    _write(settings)


def connected_integrations() -> dict[str, bool]:
    """Return which MCP-backed integrations are currently configured."""
    mcp_servers = _read().get("mcpServers", {})
    return {name: name in mcp_servers for name in _SERVERS}


def supported_integrations() -> list[str]:
    return list(_SERVERS.keys())
