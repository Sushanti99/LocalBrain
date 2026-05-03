"""Integration OAuth endpoints — writes MCP config on connect."""

from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from brain import ingest, mcp_config

if TYPE_CHECKING:
    from fastapi import FastAPI
    from brain.server import AppRuntime

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

def _get_google_client_config() -> dict:
    client_id = GOOGLE_CLIENT_ID
    client_secret = GOOGLE_CLIENT_SECRET
    if not client_id or not client_secret:
        client_id, client_secret = _load_google_credentials_from_file()
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:3000/api/integrations/google/callback"],
        }
    }

# ── GitHub ────────────────────────────────────────────────────────────────────

GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_SCOPES        = "repo notifications read:user"

# ── Slack ─────────────────────────────────────────────────────────────────────

SLACK_CLIENT_ID     = os.getenv("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET", "")
SLACK_SCOPES        = "channels:read,channels:history,im:history,users:read"

# ── Notion ────────────────────────────────────────────────────────────────────

NOTION_CLIENT_ID     = os.getenv("NOTION_CLIENT_ID", "")
NOTION_CLIENT_SECRET = os.getenv("NOTION_CLIENT_SECRET", "")

# ── shared state ──────────────────────────────────────────────────────────────

_oauth_states: dict[str, tuple] = {}
ENV_FILE = Path(".env")


# ── helpers ───────────────────────────────────────────────────────────────────

def _page(body: str, *, title: str = "brain²") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ font-family: "Iowan Old Style","Palatino Linotype",serif; }}
    body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
           background:#f6f0e8; color:#1f1b17; }}
    .card {{ background:#fff; border:1px solid #d4c3b2; border-radius:20px;
             padding:36px 40px; max-width:480px; width:100%; box-shadow:0 8px 32px rgba(74,46,25,.12); }}
    h2 {{ margin:0 0 8px; font-size:20px; }}
    p {{ color:#6c635c; font-size:15px; line-height:1.55; margin:0 0 20px; }}
    button {{ background:#bd4f2b; color:#fff; border:0; border-radius:999px;
              padding:11px 22px; font:inherit; font-size:14px; cursor:pointer; }}
    button:hover {{ background:#8c3518; }}
    .success {{ color:#1a7a50; font-weight:600; font-size:16px; margin-bottom:8px; }}
    a {{ color:#bd4f2b; }}
  </style>
</head>
<body><div class="card">{body}</div></body>
</html>"""


def _success_page(message: str = "Connected successfully.") -> str:
    return _page(f"""
  <p class="success">✓ {message}</p>
  <p>You can close this window.</p>
  <script>setTimeout(() => window.close(), 1200);</script>
""")


def _error_page(message: str) -> str:
    return _page(f"""
  <h2>Something went wrong</h2>
  <p>{message}</p>
  <button onclick="window.close()">Close</button>
""")


def _update_env(key: str, value: str) -> None:
    lines: list[str] = []
    found = False
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def _remove_env(key: str) -> None:
    if not ENV_FILE.exists():
        return
    lines = [l for l in ENV_FILE.read_text().splitlines() if not l.strip().startswith(f"{key}=")]
    ENV_FILE.write_text("\n".join(lines) + "\n")
    os.environ.pop(key, None)


# ── route registration ────────────────────────────────────────────────────────

def register(app: "FastAPI", runtime: "AppRuntime") -> None:

    def _trigger_ingest(integration_id: str) -> None:
        asyncio.create_task(
            ingest.run_ingest(runtime.app_cfg.vault.path, runtime.active_agent, integration_id, runtime.env_cfg)
        )

    # ── status ────────────────────────────────────────────────────────────────

    @app.get("/api/integrations/status")
    async def integrations_status():
        env = runtime.env_cfg
        mcp_config.sync_from_env(runtime.active_agent)
        mcp = mcp_config.connected_integrations(runtime.active_agent)
        return JSONResponse({
            "gmail":    env.google_token_file.exists(),
            "calendar": env.google_token_file.exists(),
            "notion":   bool(env.notion_api_key),
            "github":   mcp.get("github", False),
            "slack":    mcp.get("slack", False),
            "linear":   mcp.get("linear", False),
            "whatsapp": False,
            "imessage": False,
            "linkedin": False,
        })

    # ── Google OAuth ──────────────────────────────────────────────────────────

    @app.get("/api/integrations/google/connect")
    async def google_connect(request: Request):
        try:
            from google_auth_oauthlib.flow import Flow
            state = secrets.token_urlsafe(16)
            redirect_uri = str(request.base_url).rstrip("/") + "/api/integrations/google/callback"
            flow = Flow.from_client_config(_get_google_client_config(), scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri)
            auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", state=state)
            _oauth_states[state] = (flow, redirect_uri)
            return RedirectResponse(auth_url)
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))

    @app.get("/api/integrations/google/callback")
    async def google_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(_error_page(f"Google declined access: {error}"))
        entry = _oauth_states.pop(state, None)
        if not entry:
            return HTMLResponse(_error_page("Session expired. Please try connecting again."))
        flow, redirect_uri = entry
        try:
            flow.redirect_uri = redirect_uri
            flow.fetch_token(code=code)
            token_file = runtime.env_cfg.google_token_file
            token_file.write_text(flow.credentials.to_json())
            _update_env("GOOGLE_TOKEN_FILE", str(token_file))
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))
        _trigger_ingest("gmail")
        _trigger_ingest("calendar")
        return HTMLResponse(_success_page("Google connected — Gmail and Calendar are live."))

    @app.post("/api/integrations/google/disconnect")
    async def google_disconnect():
        token_file = runtime.env_cfg.google_token_file
        if token_file.exists():
            token_file.unlink()
        return JSONResponse({"status": "ok"})

    # ── GitHub OAuth ──────────────────────────────────────────────────────────

    @app.get("/api/integrations/github/connect")
    async def github_connect(request: Request):
        if not GITHUB_CLIENT_ID:
            return HTMLResponse(_error_page("GitHub OAuth not configured yet."))
        state = secrets.token_urlsafe(16)
        redirect_uri = str(request.base_url).rstrip("/") + "/api/integrations/github/callback"
        _oauth_states[state] = ("github", redirect_uri)
        auth_url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={GITHUB_CLIENT_ID}"
            f"&scope={GITHUB_SCOPES.replace(' ', '%20')}"
            f"&state={state}"
            f"&redirect_uri={redirect_uri}"
        )
        return RedirectResponse(auth_url)

    @app.get("/api/integrations/github/callback")
    async def github_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(_error_page(f"GitHub declined access: {error}"))
        entry = _oauth_states.pop(state, None)
        if not entry:
            return HTMLResponse(_error_page("Session expired. Please try connecting again."))
        _, redirect_uri = entry
        try:
            import httpx
            resp = httpx.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET,
                      "code": code, "redirect_uri": redirect_uri},
            )
            data = resp.json()
            token = data.get("access_token", "")
            if not token:
                return HTMLResponse(_error_page(f"No token returned: {data}"))
            _update_env("GITHUB_TOKEN", token)
            mcp_config.add_server("github", {"api_key": token})
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))
        _trigger_ingest("github")
        return HTMLResponse(_success_page("GitHub connected — brain² can now read your PRs and issues."))

    @app.post("/api/integrations/github/save")
    async def github_save(api_key: str = Form(...)):
        token = api_key.strip()
        if not token:
            return JSONResponse({"status": "error", "message": "Token cannot be empty."}, status_code=400)
        _update_env("GITHUB_TOKEN", token)
        mcp_config.add_server("github", {"api_key": token})
        _trigger_ingest("github")
        return JSONResponse({"status": "ok"})

    @app.post("/api/integrations/github/disconnect")
    async def github_disconnect():
        _remove_env("GITHUB_TOKEN")
        mcp_config.remove_server("github")
        return JSONResponse({"status": "ok"})

    # ── Slack OAuth ───────────────────────────────────────────────────────────

    @app.get("/api/integrations/slack/connect")
    async def slack_connect(request: Request):
        if not SLACK_CLIENT_ID:
            return HTMLResponse(_error_page("Slack OAuth not configured yet."))
        state = secrets.token_urlsafe(16)
        redirect_uri = str(request.base_url).rstrip("/") + "/api/integrations/slack/callback"
        _oauth_states[state] = ("slack", redirect_uri)
        from urllib.parse import urlencode
        params = urlencode({
            "client_id": SLACK_CLIENT_ID,
            "scope": SLACK_SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
        })
        return RedirectResponse(f"https://slack.com/oauth/v2/authorize?{params}")

    @app.get("/api/integrations/slack/callback")
    async def slack_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(_error_page(f"Slack declined access: {error}"))
        entry = _oauth_states.pop(state, None)
        if not entry:
            return HTMLResponse(_error_page("Session expired. Please try connecting again."))
        _, redirect_uri = entry
        try:
            import httpx
            resp = httpx.post(
                "https://slack.com/api/oauth.v2.access",
                data={"client_id": SLACK_CLIENT_ID, "client_secret": SLACK_CLIENT_SECRET,
                      "code": code, "redirect_uri": redirect_uri},
            )
            data = resp.json()
            if not data.get("ok"):
                return HTMLResponse(_error_page(f"Slack error: {data.get('error', 'unknown')}"))
            bot_token = data.get("access_token", "")
            team_id   = data.get("team", {}).get("id", "")
            _update_env("SLACK_BOT_TOKEN", bot_token)
            _update_env("SLACK_TEAM_ID", team_id)
            mcp_config.add_server("slack", {"bot_token": bot_token, "team_id": team_id})
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))
        _trigger_ingest("slack")
        return HTMLResponse(_success_page("Slack connected — brain² can now read your messages."))

    @app.post("/api/integrations/slack/save")
    async def slack_save(api_key: str = Form(...)):
        token = api_key.strip()
        if not token.startswith("xoxb-"):
            return JSONResponse({"status": "error", "message": "Doesn't look like a Slack bot token (should start with xoxb-)."}, status_code=400)
        _update_env("SLACK_BOT_TOKEN", token)
        mcp_config.add_server("slack", {"bot_token": token, "team_id": os.getenv("SLACK_TEAM_ID", "")})
        _trigger_ingest("slack")
        return JSONResponse({"status": "ok"})

    @app.post("/api/integrations/slack/disconnect")
    async def slack_disconnect():
        _remove_env("SLACK_BOT_TOKEN")
        _remove_env("SLACK_TEAM_ID")
        mcp_config.remove_server("slack")
        return JSONResponse({"status": "ok"})

    # ── Notion ────────────────────────────────────────────────────────────────

    @app.get("/api/integrations/notion/connect")
    async def notion_connect(request: Request):
        if not NOTION_CLIENT_ID:
            return JSONResponse({"status": "inline"})
        from urllib.parse import urlencode
        state = secrets.token_urlsafe(16)
        redirect_uri = str(request.base_url).rstrip("/") + "/api/integrations/notion/callback"
        _oauth_states[state] = (None, redirect_uri)
        params = urlencode({"client_id": NOTION_CLIENT_ID, "response_type": "code",
                            "owner": "user", "redirect_uri": redirect_uri, "state": state})
        return RedirectResponse(f"https://api.notion.com/v1/oauth/authorize?{params}")

    @app.get("/api/integrations/notion/callback")
    async def notion_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(_error_page(f"Notion declined access: {error}"))
        if state not in _oauth_states:
            return HTMLResponse(_error_page("Session expired. Please try connecting again."))
        _, redirect_uri = _oauth_states.pop(state)
        try:
            import base64
            import httpx
            creds = base64.b64encode(f"{NOTION_CLIENT_ID}:{NOTION_CLIENT_SECRET}".encode()).decode()
            resp = httpx.post(
                "https://api.notion.com/v1/oauth/token",
                headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
                json={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            )
            data = resp.json()
            token = data.get("access_token", "")
            if not token:
                return HTMLResponse(_error_page(f"No token: {data}"))
            _update_env("NOTION_API_KEY", token)
            runtime.env_cfg.notion_api_key = token
            mcp_config.add_server("notion", {"api_key": token})
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))
        _trigger_ingest("notion")
        return HTMLResponse(_success_page("Notion connected."))

    @app.post("/api/integrations/notion/save")
    async def notion_save(api_key: str = Form(...)):
        key = api_key.strip()
        if not (key.startswith("secret_") or key.startswith("ntn_")):
            return JSONResponse({"status": "error", "message": "Doesn't look like a Notion secret (should start with secret_ or ntn_)."}, status_code=400)
        _update_env("NOTION_API_KEY", key)
        runtime.env_cfg.notion_api_key = key
        mcp_config.add_server("notion", {"api_key": key})
        _trigger_ingest("notion")
        return JSONResponse({"status": "ok"})

    @app.post("/api/integrations/notion/disconnect")
    async def notion_disconnect():
        _remove_env("NOTION_API_KEY")
        runtime.env_cfg.notion_api_key = ""
        mcp_config.remove_server("notion")
        return JSONResponse({"status": "ok"})

    # ── Linear ───────────────────────────────────────────────────────────────

    @app.post("/api/integrations/linear/save")
    async def linear_save(api_key: str = Form(...)):
        key = api_key.strip()
        if not key:
            return JSONResponse({"status": "error", "message": "API key cannot be empty."}, status_code=400)
        _update_env("LINEAR_API_KEY", key)
        mcp_config.add_server("linear", {"api_key": key})
        _trigger_ingest("linear")
        return JSONResponse({"status": "ok"})

    @app.post("/api/integrations/linear/disconnect")
    async def linear_disconnect():
        _remove_env("LINEAR_API_KEY")
        mcp_config.remove_server("linear")
        return JSONResponse({"status": "ok"})

    # ── fallback ──────────────────────────────────────────────────────────────

    @app.get("/api/integrations/{integration_id}/connect")
    async def generic_connect(integration_id: str):
        return HTMLResponse(_page(f"""
  <h2>{integration_id.title()} — Coming Soon</h2>
  <p>This integration is on the roadmap.</p>
  <button onclick="window.close()">Close</button>
"""))

    @app.post("/api/integrations/{integration_id}/disconnect")
    async def generic_disconnect(integration_id: str):
        mcp_config.remove_server(integration_id)
        return JSONResponse({"status": "ok"})
