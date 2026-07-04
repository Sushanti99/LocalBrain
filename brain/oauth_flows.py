"""Transport-agnostic OAuth/API-key flows for third-party integrations.

No FastAPI, no argparse — these functions are called by both the web routes
(`brain/integrations_api.py`) and the CLI (`brain/cli.py`, via `integrations_core.py`).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from brain.models import EnvConfig


@dataclass
class ProviderCredentials:
    integration_id: str
    mcp_credentials: dict[str, str]
    env_updates: dict[str, str]
    ingest_ids: list[str] = field(default_factory=list)


# ── Google ────────────────────────────────────────────────────────────────────

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")


def _load_google_credentials_from_file() -> tuple[str, str]:
    """Read client_id/secret from credentials.json if env vars are not set."""
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "")
    if not creds_file:
        return "", ""
    try:
        import json
        data = json.loads(Path(creds_file).read_text())
        cfg = data.get("web") or data.get("installed") or {}
        return cfg.get("client_id", ""), cfg.get("client_secret", "")
    except Exception:
        return "", ""


def get_google_client_config(redirect_uri: str) -> dict:
    import sys as _sys
    client_id = GOOGLE_CLIENT_ID
    client_secret = GOOGLE_CLIENT_SECRET
    if not client_id or not client_secret:
        client_id, client_secret = _load_google_credentials_from_file()
    if not client_id or not client_secret:
        candidates = [
            Path(getattr(_sys, "_MEIPASS", "")) / "credentials.json",
            Path(__file__).parent.parent / "credentials.json",
        ]
        for p in candidates:
            if p.exists():
                try:
                    import json as _json
                    data = _json.loads(p.read_text())
                    cfg = data.get("web") or data.get("installed") or {}
                    client_id = cfg.get("client_id", "")
                    client_secret = cfg.get("client_secret", "")
                    if client_id:
                        break
                except Exception:
                    pass
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def google_oauth_configured() -> bool:
    client_id, client_secret = GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    if not client_id or not client_secret:
        client_id, client_secret = _load_google_credentials_from_file()
    return bool(client_id and client_secret)


@dataclass
class GoogleAuthContext:
    flow: Any
    redirect_uri: str


def google_build_auth_url(redirect_uri: str) -> tuple[str, str, GoogleAuthContext]:
    import secrets
    from google_auth_oauthlib.flow import Flow
    cfg = get_google_client_config(redirect_uri)
    state = secrets.token_urlsafe(16)
    flow = Flow.from_client_config(cfg, scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri)
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", state=state)
    return auth_url, state, GoogleAuthContext(flow=flow, redirect_uri=redirect_uri)


def google_exchange_code(context: GoogleAuthContext, code: str, env_cfg: EnvConfig) -> ProviderCredentials:
    context.flow.redirect_uri = context.redirect_uri
    context.flow.fetch_token(code=code)
    token_file = env_cfg.google_token_file
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(context.flow.credentials.to_json())
    return ProviderCredentials(
        integration_id="google",
        mcp_credentials={
            "token_file": str(token_file),
            "credentials_file": str(env_cfg.google_credentials_file),
        },
        env_updates={"GOOGLE_TOKEN_FILE": str(token_file)},
        ingest_ids=["gmail", "calendar"],
    )


# ── GitHub ────────────────────────────────────────────────────────────────────

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_SCOPES = "repo notifications read:user"


def github_oauth_configured() -> bool:
    return bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)


def github_build_auth_url(redirect_uri: str) -> tuple[str, str, str]:
    import secrets
    state = secrets.token_urlsafe(16)
    auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&scope={GITHUB_SCOPES.replace(' ', '%20')}"
        f"&state={state}"
        f"&redirect_uri={redirect_uri}"
    )
    return auth_url, state, redirect_uri


def github_exchange_code(redirect_uri: str, code: str, env_cfg: EnvConfig) -> ProviderCredentials:
    resp = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET,
              "code": code, "redirect_uri": redirect_uri},
    )
    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"No token returned: {data}")
    return _github_credentials(token)


def _github_credentials(token: str) -> ProviderCredentials:
    return ProviderCredentials(
        integration_id="github",
        mcp_credentials={"api_key": token},
        env_updates={"GITHUB_TOKEN": token},
        ingest_ids=["github"],
    )


def github_credentials_from_key(api_key: str) -> ProviderCredentials | str:
    token = api_key.strip()
    if not token:
        return "Token cannot be empty."
    return _github_credentials(token)


# ── Slack ─────────────────────────────────────────────────────────────────────

SLACK_CLIENT_ID = os.getenv("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET", "")
SLACK_SCOPES = "channels:read,channels:history,im:history,users:read"


def slack_oauth_configured() -> bool:
    return bool(SLACK_CLIENT_ID and SLACK_CLIENT_SECRET)


def slack_build_auth_url(redirect_uri: str) -> tuple[str, str, str]:
    import secrets
    state = secrets.token_urlsafe(16)
    params = urlencode({
        "client_id": SLACK_CLIENT_ID,
        "scope": SLACK_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return f"https://slack.com/oauth/v2/authorize?{params}", state, redirect_uri


def slack_exchange_code(redirect_uri: str, code: str, env_cfg: EnvConfig) -> ProviderCredentials:
    resp = httpx.post(
        "https://slack.com/api/oauth.v2.access",
        data={"client_id": SLACK_CLIENT_ID, "client_secret": SLACK_CLIENT_SECRET,
              "code": code, "redirect_uri": redirect_uri},
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack error: {data.get('error', 'unknown')}")
    bot_token = data.get("access_token", "")
    team_id = data.get("team", {}).get("id", "")
    return _slack_credentials(bot_token, team_id)


def _slack_credentials(bot_token: str, team_id: str) -> ProviderCredentials:
    return ProviderCredentials(
        integration_id="slack",
        mcp_credentials={"bot_token": bot_token, "team_id": team_id},
        env_updates={"SLACK_BOT_TOKEN": bot_token, "SLACK_TEAM_ID": team_id},
        ingest_ids=["slack"],
    )


def slack_credentials_from_key(api_key: str) -> ProviderCredentials | str:
    token = api_key.strip()
    if not token.startswith("xoxb-"):
        return "Doesn't look like a Slack bot token (should start with xoxb-)."
    return _slack_credentials(token, os.getenv("SLACK_TEAM_ID", ""))


# ── Notion ────────────────────────────────────────────────────────────────────

NOTION_CLIENT_ID = os.getenv("NOTION_CLIENT_ID", "")
NOTION_CLIENT_SECRET = os.getenv("NOTION_CLIENT_SECRET", "")


def notion_oauth_configured() -> bool:
    return bool(NOTION_CLIENT_ID and NOTION_CLIENT_SECRET)


def notion_build_auth_url(redirect_uri: str) -> tuple[str, str, str]:
    import secrets
    state = secrets.token_urlsafe(16)
    params = urlencode({"client_id": NOTION_CLIENT_ID, "response_type": "code",
                        "owner": "user", "redirect_uri": redirect_uri, "state": state})
    return f"https://api.notion.com/v1/oauth/authorize?{params}", state, redirect_uri


def notion_exchange_code(redirect_uri: str, code: str, env_cfg: EnvConfig) -> ProviderCredentials:
    creds = base64.b64encode(f"{NOTION_CLIENT_ID}:{NOTION_CLIENT_SECRET}".encode()).decode()
    resp = httpx.post(
        "https://api.notion.com/v1/oauth/token",
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
        json={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
    )
    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"No token: {data}")
    return _notion_credentials(token)


def _notion_credentials(token: str) -> ProviderCredentials:
    return ProviderCredentials(
        integration_id="notion",
        mcp_credentials={"api_key": token},
        env_updates={"NOTION_API_KEY": token},
        ingest_ids=["notion"],
    )


def notion_credentials_from_key(api_key: str) -> ProviderCredentials | str:
    key = api_key.strip()
    if not (key.startswith("secret_") or key.startswith("ntn_")):
        return "Doesn't look like a Notion secret (should start with secret_ or ntn_)."
    return _notion_credentials(key)


# ── Linear ────────────────────────────────────────────────────────────────────

LINEAR_CLIENT_ID = os.getenv("LINEAR_CLIENT_ID", "")
LINEAR_CLIENT_SECRET = os.getenv("LINEAR_CLIENT_SECRET", "")
LINEAR_SCOPES = "read"


def linear_oauth_configured() -> bool:
    return bool(LINEAR_CLIENT_ID and LINEAR_CLIENT_SECRET)


def linear_build_auth_url(redirect_uri: str) -> tuple[str, str, str]:
    import secrets
    state = secrets.token_urlsafe(16)
    params = urlencode({
        "client_id": LINEAR_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": LINEAR_SCOPES,
        "state": state,
    })
    return f"https://linear.app/oauth/authorize?{params}", state, redirect_uri


def linear_exchange_code(redirect_uri: str, code: str, env_cfg: EnvConfig) -> ProviderCredentials:
    resp = httpx.post(
        "https://api.linear.app/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": LINEAR_CLIENT_ID,
            "client_secret": LINEAR_CLIENT_SECRET,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        raise RuntimeError(f"No token returned: {data}")
    return _linear_credentials(token)


def _linear_credentials(token: str) -> ProviderCredentials:
    return ProviderCredentials(
        integration_id="linear",
        mcp_credentials={"api_key": token},
        env_updates={"LINEAR_API_KEY": token},
        ingest_ids=["linear"],
    )


def linear_credentials_from_key(api_key: str) -> ProviderCredentials | str:
    key = api_key.strip()
    if not key:
        return "API key cannot be empty."
    return _linear_credentials(key)
