"""Shared integration-connect orchestration, used by both the CLI and the web routes.

Funnels every provider through the same two writes: `env_config.update_env`
(the .env file) and `mcp_config.add_server` (the MCP config for whichever
agent backend(s) are active) — so a credential connected via one surface is
immediately visible to the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from brain import env_config, mcp_config, oauth_flows, oauth_loopback
from brain.oauth_flows import ProviderCredentials


@dataclass
class ProviderSpec:
    id: str
    label: str
    oauth_configured: Callable[[], bool]
    build_auth_url: Callable[[str], tuple] | None
    exchange_code: Callable | None
    credentials_from_key: Callable[[str], "ProviderCredentials | str"] | None
    key_prompt: str | None


PROVIDERS: dict[str, ProviderSpec] = {
    "google": ProviderSpec(
        id="google", label="Google (Gmail + Calendar)",
        oauth_configured=oauth_flows.google_oauth_configured,
        build_auth_url=oauth_flows.google_build_auth_url,
        exchange_code=oauth_flows.google_exchange_code,
        credentials_from_key=None,
        key_prompt=None,
    ),
    "github": ProviderSpec(
        id="github", label="GitHub",
        oauth_configured=oauth_flows.github_oauth_configured,
        build_auth_url=oauth_flows.github_build_auth_url,
        exchange_code=oauth_flows.github_exchange_code,
        credentials_from_key=oauth_flows.github_credentials_from_key,
        key_prompt="Paste your GitHub personal access token: ",
    ),
    "slack": ProviderSpec(
        id="slack", label="Slack",
        oauth_configured=oauth_flows.slack_oauth_configured,
        build_auth_url=oauth_flows.slack_build_auth_url,
        exchange_code=oauth_flows.slack_exchange_code,
        credentials_from_key=oauth_flows.slack_credentials_from_key,
        key_prompt="Paste your Slack bot token (xoxb-...): ",
    ),
    "notion": ProviderSpec(
        id="notion", label="Notion",
        oauth_configured=oauth_flows.notion_oauth_configured,
        build_auth_url=oauth_flows.notion_build_auth_url,
        exchange_code=oauth_flows.notion_exchange_code,
        credentials_from_key=oauth_flows.notion_credentials_from_key,
        key_prompt="Paste your Notion integration secret: ",
    ),
    "linear": ProviderSpec(
        id="linear", label="Linear",
        oauth_configured=oauth_flows.linear_oauth_configured,
        build_auth_url=oauth_flows.linear_build_auth_url,
        exchange_code=oauth_flows.linear_exchange_code,
        credentials_from_key=oauth_flows.linear_credentials_from_key,
        key_prompt="Paste your Linear personal API key: ",
    ),
}


def connect_via_browser(
    spec: ProviderSpec, *, port: int, open_browser: bool = True, timeout: float = 180
) -> ProviderCredentials:
    if spec.build_auth_url is None or spec.exchange_code is None:
        raise RuntimeError(f"{spec.label} does not support browser-based connect.")

    redirect_uri = f"http://localhost:{port}/api/integrations/{spec.id}/callback"
    callback_path = f"/api/integrations/{spec.id}/callback"
    auth_url, expected_state, context = spec.build_auth_url(redirect_uri)

    try:
        with oauth_loopback.start(port, callback_path) as listener:
            if open_browser:
                print(f"Opening your browser to connect {spec.label}...")
                oauth_loopback.open_browser(auth_url)
            else:
                print(f"Open this URL to connect {spec.label}:\n  {auth_url}")
            result = listener.wait(timeout_seconds=timeout)
    except OSError as exc:
        raise RuntimeError(
            f"Port {port} is already in use — if `brain start` is running, connect "
            f"from the web UI instead, or stop it and re-run `brain connect {spec.id}`."
        ) from exc

    if result.error:
        raise RuntimeError(f"{spec.label} declined access: {result.error}")
    if result.state != expected_state:
        raise RuntimeError("OAuth state mismatch — please try connecting again.")

    env_cfg = env_config.load_env_config()
    return spec.exchange_code(context, result.code, env_cfg)


def apply_credentials(creds: ProviderCredentials, *, agents: Iterable[str] | str | None = None) -> None:
    for key, value in creds.env_updates.items():
        env_config.update_env(key, value)
    if creds.mcp_credentials:
        mcp_config.add_server(creds.integration_id, creds.mcp_credentials, agents=agents)


# .env keys each provider writes via oauth_flows' ProviderCredentials.env_updates —
# distinct from mcp_config's env_map, which names env vars for the MCP *subprocess*,
# not our own .env file.
_ENV_KEYS_BY_PROVIDER: dict[str, list[str]] = {
    "github": ["GITHUB_TOKEN"],
    "slack": ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
    "notion": ["NOTION_API_KEY"],
    "linear": ["LINEAR_API_KEY"],
}


def disconnect_integration(integration_id: str, *, agents: Iterable[str] | str | None = None) -> None:
    for key in _ENV_KEYS_BY_PROVIDER.get(integration_id, []):
        env_config.remove_env(key)
    mcp_config.remove_server(integration_id, agents=agents)
    if integration_id == "google":
        env_cfg = env_config.load_env_config()
        if env_cfg.google_token_file.exists():
            env_cfg.google_token_file.unlink()


def compute_status(agent: str | None = None) -> dict[str, bool]:
    env_cfg = env_config.load_env_config()
    mcp_config.sync_from_env(agent)
    mcp = mcp_config.connected_integrations(agent)
    return {
        "gmail": env_cfg.google_token_file.exists(),
        "calendar": env_cfg.google_token_file.exists(),
        "notion": bool(env_cfg.notion_api_key),
        "github": mcp.get("github", False),
        "slack": mcp.get("slack", False),
        "linear": mcp.get("linear", False),
        "whatsapp": False,
        "imessage": False,
        "linkedin": False,
    }
