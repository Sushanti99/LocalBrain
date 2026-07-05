from brain import integrations_core, mcp_config
from brain.oauth_flows import ProviderCredentials


def test_apply_credentials_writes_env_and_mcp_config(tmp_path, monkeypatch):
    claude_settings = tmp_path / "claude-settings.json"
    codex_config = tmp_path / "codex-config.toml"
    monkeypatch.setattr(mcp_config, "CLAUDE_CONFIG", claude_settings)
    monkeypatch.setattr(mcp_config, "CODEX_CONFIG", codex_config)
    env_file = tmp_path / ".env"
    monkeypatch.setattr("brain.env_config.resolve_env_file", lambda: env_file)

    creds = ProviderCredentials(
        integration_id="github",
        mcp_credentials={"api_key": "ghp_test"},
        env_updates={"GITHUB_TOKEN": "ghp_test"},
    )
    integrations_core.apply_credentials(creds)

    assert "GITHUB_TOKEN=ghp_test" in env_file.read_text()
    assert '"github"' in claude_settings.read_text(encoding="utf-8")


def test_disconnect_integration_removes_env_and_mcp_config(tmp_path, monkeypatch):
    claude_settings = tmp_path / "claude-settings.json"
    codex_config = tmp_path / "codex-config.toml"
    monkeypatch.setattr(mcp_config, "CLAUDE_CONFIG", claude_settings)
    monkeypatch.setattr(mcp_config, "CODEX_CONFIG", codex_config)
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=ghp_test\nOTHER=keep\n")
    monkeypatch.setattr("brain.env_config.resolve_env_file", lambda: env_file)

    mcp_config.add_server("github", {"api_key": "ghp_test"})
    integrations_core.disconnect_integration("github")

    assert "GITHUB_TOKEN" not in env_file.read_text()
    assert "OTHER=keep" in env_file.read_text()
    assert '"github"' not in claude_settings.read_text(encoding="utf-8")


class _StubListener:
    def __init__(self, result):
        self._result = result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait(self, timeout_seconds=180):
        return self._result


def test_connect_via_browser_happy_path(monkeypatch):
    from brain import oauth_loopback

    class FakeResult:
        code = "abc123"
        state = "expected-state"
        error = None

    monkeypatch.setattr(oauth_loopback, "start", lambda port, path: _StubListener(FakeResult()))
    monkeypatch.setattr(oauth_loopback, "open_browser", lambda url: None)
    monkeypatch.setattr("brain.env_config.load_env_config", lambda: None)

    def build_auth_url(redirect_uri):
        return "https://example.com/authorize", "expected-state", redirect_uri

    def exchange_code(context, code, env_cfg):
        return ProviderCredentials(integration_id="test", mcp_credentials={}, env_updates={"CODE": code})

    spec = integrations_core.ProviderSpec(
        id="test", label="Test Provider", oauth_configured=lambda: True,
        build_auth_url=build_auth_url, exchange_code=exchange_code,
        credentials_from_key=None, key_prompt=None,
    )

    creds = integrations_core.connect_via_browser(spec, port=9999)
    assert creds.env_updates == {"CODE": "abc123"}


def test_connect_via_browser_raises_on_state_mismatch(monkeypatch):
    from brain import oauth_loopback

    class FakeResult:
        code = "abc123"
        state = "wrong-state"
        error = None

    monkeypatch.setattr(oauth_loopback, "start", lambda port, path: _StubListener(FakeResult()))
    monkeypatch.setattr(oauth_loopback, "open_browser", lambda url: None)

    spec = integrations_core.ProviderSpec(
        id="test", label="Test Provider", oauth_configured=lambda: True,
        build_auth_url=lambda redirect_uri: ("https://example.com", "expected-state", redirect_uri),
        exchange_code=lambda context, code, env_cfg: None,
        credentials_from_key=None, key_prompt=None,
    )

    try:
        integrations_core.connect_via_browser(spec, port=9999)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "state mismatch" in str(exc)


def test_connect_via_browser_port_in_use_raises_actionable_error(monkeypatch):
    from brain import oauth_loopback

    def _raise_os_error(port, path):
        raise OSError("address already in use")

    monkeypatch.setattr(oauth_loopback, "start", _raise_os_error)

    spec = integrations_core.ProviderSpec(
        id="google", label="Google", oauth_configured=lambda: True,
        build_auth_url=lambda redirect_uri: ("https://example.com", "state", redirect_uri),
        exchange_code=lambda context, code, env_cfg: None,
        credentials_from_key=None, key_prompt=None,
    )

    try:
        integrations_core.connect_via_browser(spec, port=6683)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "already in use" in str(exc)
