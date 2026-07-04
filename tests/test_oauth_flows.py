from brain import oauth_flows


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


# ── credentials_from_key validation ──────────────────────────────────────────

def test_github_credentials_from_key_rejects_empty():
    assert oauth_flows.github_credentials_from_key("  ") == "Token cannot be empty."


def test_github_credentials_from_key_accepts_token():
    creds = oauth_flows.github_credentials_from_key("ghp_test")
    assert creds.integration_id == "github"
    assert creds.mcp_credentials == {"api_key": "ghp_test"}
    assert creds.env_updates == {"GITHUB_TOKEN": "ghp_test"}


def test_slack_credentials_from_key_rejects_wrong_prefix():
    result = oauth_flows.slack_credentials_from_key("not-a-token")
    assert "xoxb-" in result


def test_slack_credentials_from_key_accepts_bot_token(monkeypatch):
    monkeypatch.delenv("SLACK_TEAM_ID", raising=False)
    creds = oauth_flows.slack_credentials_from_key("xoxb-test")
    assert creds.env_updates["SLACK_BOT_TOKEN"] == "xoxb-test"


def test_notion_credentials_from_key_rejects_wrong_prefix():
    result = oauth_flows.notion_credentials_from_key("wrong")
    assert "secret_" in result


def test_notion_credentials_from_key_accepts_secret():
    creds = oauth_flows.notion_credentials_from_key("secret_abc")
    assert creds.env_updates == {"NOTION_API_KEY": "secret_abc"}


def test_linear_credentials_from_key_rejects_empty():
    assert oauth_flows.linear_credentials_from_key("") == "API key cannot be empty."


def test_linear_credentials_from_key_accepts_key():
    creds = oauth_flows.linear_credentials_from_key("lin_api_test")
    assert creds.integration_id == "linear"
    assert creds.mcp_credentials == {"api_key": "lin_api_test"}
    assert creds.env_updates == {"LINEAR_API_KEY": "lin_api_test"}


# ── OAuth build/exchange (network mocked) ────────────────────────────────────

def test_github_build_auth_url_includes_client_id(monkeypatch):
    monkeypatch.setattr(oauth_flows, "GITHUB_CLIENT_ID", "client123")
    auth_url, state, context = oauth_flows.github_build_auth_url("http://localhost:6683/api/integrations/github/callback")
    assert "client_id=client123" in auth_url
    assert state
    assert context == "http://localhost:6683/api/integrations/github/callback"


def test_github_exchange_code_returns_credentials(monkeypatch):
    monkeypatch.setattr(oauth_flows, "GITHUB_CLIENT_ID", "client123")
    monkeypatch.setattr(oauth_flows, "GITHUB_CLIENT_SECRET", "secret456")
    monkeypatch.setattr(oauth_flows.httpx, "post", lambda *a, **k: _FakeResponse({"access_token": "ghp_exchanged"}))

    creds = oauth_flows.github_exchange_code("http://localhost:6683/cb", "code123", env_cfg=None)
    assert creds.env_updates == {"GITHUB_TOKEN": "ghp_exchanged"}


def test_github_exchange_code_raises_without_token(monkeypatch):
    monkeypatch.setattr(oauth_flows.httpx, "post", lambda *a, **k: _FakeResponse({"error": "bad_code"}))
    try:
        oauth_flows.github_exchange_code("http://localhost:6683/cb", "bad", env_cfg=None)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_linear_build_auth_url_includes_client_id_and_scope(monkeypatch):
    monkeypatch.setattr(oauth_flows, "LINEAR_CLIENT_ID", "linclient")
    auth_url, state, context = oauth_flows.linear_build_auth_url("http://localhost:6683/api/integrations/linear/callback")
    assert "linear.app/oauth/authorize" in auth_url
    assert "client_id=linclient" in auth_url
    assert "scope=read" in auth_url


def test_linear_exchange_code_returns_credentials(monkeypatch):
    monkeypatch.setattr(oauth_flows, "LINEAR_CLIENT_ID", "linclient")
    monkeypatch.setattr(oauth_flows, "LINEAR_CLIENT_SECRET", "linsecret")
    monkeypatch.setattr(oauth_flows.httpx, "post", lambda *a, **k: _FakeResponse({"access_token": "lin_oauth_token"}))

    creds = oauth_flows.linear_exchange_code("http://localhost:6683/cb", "code123", env_cfg=None)
    assert creds.integration_id == "linear"
    assert creds.mcp_credentials == {"api_key": "lin_oauth_token"}
    assert creds.env_updates == {"LINEAR_API_KEY": "lin_oauth_token"}
