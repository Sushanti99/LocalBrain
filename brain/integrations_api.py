"""Integration OAuth endpoints — thin FastAPI wrappers around the shared core.

Auth-url building and code exchange live in `brain/oauth_flows.py`; credential
persistence (writing .env + MCP config) lives in `brain/integrations_core.py`.
Both are transport-agnostic and shared with the CLI (`brain connect <provider>`).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from brain import ingest, integrations_core, mcp_config, oauth_flows

if TYPE_CHECKING:
    from fastapi import FastAPI
    from brain.server import AppRuntime

# ── shared state ──────────────────────────────────────────────────────────────

_oauth_states: dict[str, tuple] = {}


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


def _github_slack_notion_linear_redirect_uri(request: Request, provider: str) -> str:
    return str(request.base_url).rstrip("/") + f"/api/integrations/{provider}/callback"


# ── route registration ────────────────────────────────────────────────────────

def register(app: "FastAPI", runtime: "AppRuntime") -> None:
    def _trigger_ingest(integration_id: str) -> None:
        asyncio.create_task(
            ingest.run_ingest(runtime.app_cfg.vault.path, runtime.active_agent, integration_id, runtime.env_cfg)
        )

    def _finish_connect(creds: oauth_flows.ProviderCredentials, *, success_message: str) -> str:
        integrations_core.apply_credentials(creds, agents=runtime.active_agent)
        for integration_id in creds.ingest_ids:
            _trigger_ingest(integration_id)
        return success_message

    # ── status ────────────────────────────────────────────────────────────────

    @app.get("/api/integrations/status")
    async def integrations_status():
        return JSONResponse(integrations_core.compute_status(runtime.active_agent))

    # ── Google OAuth ──────────────────────────────────────────────────────────

    def _google_redirect_uri(request: Request) -> str:
        port = request.url.port or 80
        return f"http://localhost:{port}/api/integrations/google/callback"

    @app.get("/api/integrations/google/auth-url")
    async def google_auth_url(request: Request):
        try:
            auth_url, state, context = oauth_flows.google_build_auth_url(_google_redirect_uri(request))
            _oauth_states[state] = context
            return JSONResponse({"url": auth_url})
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/integrations/google/connect")
    async def google_connect(request: Request):
        try:
            auth_url, state, context = oauth_flows.google_build_auth_url(_google_redirect_uri(request))
            _oauth_states[state] = context
            return RedirectResponse(auth_url)
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))

    @app.get("/api/integrations/google/callback")
    async def google_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(_error_page(f"Google declined access: {error}"))
        context = _oauth_states.pop(state, None)
        if context is None:
            return HTMLResponse(_error_page("Session expired. Please try connecting again."))
        try:
            creds = oauth_flows.google_exchange_code(context, code, runtime.env_cfg)
            message = _finish_connect(creds, success_message="Google connected — Gmail and Calendar are live.")
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))
        return HTMLResponse(_success_page(message))

    @app.post("/api/integrations/google/disconnect")
    async def google_disconnect():
        integrations_core.disconnect_integration("google", agents=runtime.active_agent)
        return JSONResponse({"status": "ok"})

    # ── GitHub OAuth ──────────────────────────────────────────────────────────

    @app.get("/api/integrations/github/connect")
    async def github_connect(request: Request):
        if not oauth_flows.github_oauth_configured():
            return HTMLResponse(_error_page("GitHub OAuth not configured yet."))
        redirect_uri = _github_slack_notion_linear_redirect_uri(request, "github")
        auth_url, state, context = oauth_flows.github_build_auth_url(redirect_uri)
        _oauth_states[state] = context
        return RedirectResponse(auth_url)

    @app.get("/api/integrations/github/callback")
    async def github_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(_error_page(f"GitHub declined access: {error}"))
        context = _oauth_states.pop(state, None)
        if context is None:
            return HTMLResponse(_error_page("Session expired. Please try connecting again."))
        try:
            creds = oauth_flows.github_exchange_code(context, code, runtime.env_cfg)
            message = _finish_connect(creds, success_message="GitHub connected — brain² can now read your PRs and issues.")
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))
        return HTMLResponse(_success_page(message))

    @app.post("/api/integrations/github/save")
    async def github_save(api_key: str = Form(...)):
        result = oauth_flows.github_credentials_from_key(api_key)
        if isinstance(result, str):
            return JSONResponse({"status": "error", "message": result}, status_code=400)
        _finish_connect(result, success_message="GitHub connected.")
        return JSONResponse({"status": "ok"})

    @app.post("/api/integrations/github/disconnect")
    async def github_disconnect():
        integrations_core.disconnect_integration("github", agents=runtime.active_agent)
        return JSONResponse({"status": "ok"})

    # ── Slack OAuth ───────────────────────────────────────────────────────────

    @app.get("/api/integrations/slack/connect")
    async def slack_connect(request: Request):
        if not oauth_flows.slack_oauth_configured():
            return HTMLResponse(_error_page("Slack OAuth not configured yet."))
        redirect_uri = _github_slack_notion_linear_redirect_uri(request, "slack")
        auth_url, state, context = oauth_flows.slack_build_auth_url(redirect_uri)
        _oauth_states[state] = context
        return RedirectResponse(auth_url)

    @app.get("/api/integrations/slack/callback")
    async def slack_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(_error_page(f"Slack declined access: {error}"))
        context = _oauth_states.pop(state, None)
        if context is None:
            return HTMLResponse(_error_page("Session expired. Please try connecting again."))
        try:
            creds = oauth_flows.slack_exchange_code(context, code, runtime.env_cfg)
            message = _finish_connect(creds, success_message="Slack connected — brain² can now read your messages.")
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))
        return HTMLResponse(_success_page(message))

    @app.post("/api/integrations/slack/save")
    async def slack_save(api_key: str = Form(...)):
        result = oauth_flows.slack_credentials_from_key(api_key)
        if isinstance(result, str):
            return JSONResponse({"status": "error", "message": result}, status_code=400)
        _finish_connect(result, success_message="Slack connected.")
        return JSONResponse({"status": "ok"})

    @app.post("/api/integrations/slack/disconnect")
    async def slack_disconnect():
        integrations_core.disconnect_integration("slack", agents=runtime.active_agent)
        return JSONResponse({"status": "ok"})

    # ── Notion ────────────────────────────────────────────────────────────────

    @app.get("/api/integrations/notion/connect")
    async def notion_connect(request: Request):
        if not oauth_flows.notion_oauth_configured():
            return JSONResponse({"status": "inline"})
        redirect_uri = _github_slack_notion_linear_redirect_uri(request, "notion")
        auth_url, state, context = oauth_flows.notion_build_auth_url(redirect_uri)
        _oauth_states[state] = context
        return RedirectResponse(auth_url)

    @app.get("/api/integrations/notion/callback")
    async def notion_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(_error_page(f"Notion declined access: {error}"))
        context = _oauth_states.pop(state, None)
        if context is None:
            return HTMLResponse(_error_page("Session expired. Please try connecting again."))
        try:
            creds = oauth_flows.notion_exchange_code(context, code, runtime.env_cfg)
            message = _finish_connect(creds, success_message="Notion connected.")
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))
        return HTMLResponse(_success_page(message))

    @app.post("/api/integrations/notion/save")
    async def notion_save(api_key: str = Form(...)):
        result = oauth_flows.notion_credentials_from_key(api_key)
        if isinstance(result, str):
            return JSONResponse({"status": "error", "message": result}, status_code=400)
        runtime.env_cfg.notion_api_key = result.env_updates.get("NOTION_API_KEY", runtime.env_cfg.notion_api_key)
        _finish_connect(result, success_message="Notion connected.")
        return JSONResponse({"status": "ok"})

    @app.post("/api/integrations/notion/disconnect")
    async def notion_disconnect():
        integrations_core.disconnect_integration("notion", agents=runtime.active_agent)
        runtime.env_cfg.notion_api_key = ""
        return JSONResponse({"status": "ok"})

    # ── Linear ───────────────────────────────────────────────────────────────

    @app.get("/api/integrations/linear/connect")
    async def linear_connect(request: Request):
        if not oauth_flows.linear_oauth_configured():
            return JSONResponse({"status": "inline"})
        redirect_uri = _github_slack_notion_linear_redirect_uri(request, "linear")
        auth_url, state, context = oauth_flows.linear_build_auth_url(redirect_uri)
        _oauth_states[state] = context
        return RedirectResponse(auth_url)

    @app.get("/api/integrations/linear/callback")
    async def linear_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(_error_page(f"Linear declined access: {error}"))
        context = _oauth_states.pop(state, None)
        if context is None:
            return HTMLResponse(_error_page("Session expired. Please try connecting again."))
        try:
            creds = oauth_flows.linear_exchange_code(context, code, runtime.env_cfg)
            message = _finish_connect(creds, success_message="Linear connected.")
        except Exception as exc:
            return HTMLResponse(_error_page(str(exc)))
        return HTMLResponse(_success_page(message))

    @app.post("/api/integrations/linear/save")
    async def linear_save(api_key: str = Form(...)):
        result = oauth_flows.linear_credentials_from_key(api_key)
        if isinstance(result, str):
            return JSONResponse({"status": "error", "message": result}, status_code=400)
        _finish_connect(result, success_message="Linear connected.")
        return JSONResponse({"status": "ok"})

    @app.post("/api/integrations/linear/disconnect")
    async def linear_disconnect():
        integrations_core.disconnect_integration("linear", agents=runtime.active_agent)
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
