from brain import mcp_config


def test_add_server_writes_stdio_server_for_both_backends(tmp_path, monkeypatch):
    claude_settings = tmp_path / "claude-settings.json"
    codex_config = tmp_path / "codex-config.toml"
    monkeypatch.setattr(mcp_config, "CLAUDE_SETTINGS", claude_settings)
    monkeypatch.setattr(mcp_config, "CODEX_CONFIG", codex_config)

    mcp_config.add_server("github", {"api_key": "ghp_test"})

    assert '"github"' in claude_settings.read_text(encoding="utf-8")
    codex_text = codex_config.read_text(encoding="utf-8")
    assert "[mcp_servers.github]" in codex_text
    assert 'command = "npx"' in codex_text
    assert 'args = ["-y", "@modelcontextprotocol/server-github"]' in codex_text
    assert 'GITHUB_PERSONAL_ACCESS_TOKEN = "ghp_test"' in codex_text


def test_add_server_writes_linear_remote_codex_config(tmp_path, monkeypatch):
    claude_settings = tmp_path / "claude-settings.json"
    codex_config = tmp_path / "codex-config.toml"
    monkeypatch.setattr(mcp_config, "CLAUDE_SETTINGS", claude_settings)
    monkeypatch.setattr(mcp_config, "CODEX_CONFIG", codex_config)

    mcp_config.add_server("linear", {"api_key": "lin_api_test"}, agents="codex")

    codex_text = codex_config.read_text(encoding="utf-8")
    assert "[mcp_servers.linear]" in codex_text
    assert 'url = "https://mcp.linear.app/mcp"' in codex_text
    assert 'bearer_token_env_var = "LINEAR_API_KEY"' in codex_text
    assert "[features]" in codex_text
    assert "experimental_use_rmcp_client = true" in codex_text
    assert "[mcp_servers.linear.env]" not in codex_text
    assert not claude_settings.exists()


def test_connected_integrations_are_backend_specific(tmp_path, monkeypatch):
    claude_settings = tmp_path / "claude-settings.json"
    codex_config = tmp_path / "codex-config.toml"
    monkeypatch.setattr(mcp_config, "CLAUDE_SETTINGS", claude_settings)
    monkeypatch.setattr(mcp_config, "CODEX_CONFIG", codex_config)

    mcp_config.add_server("github", {"api_key": "ghp_claude"}, agents="claude-code")
    mcp_config.add_server("linear", {"api_key": "lin_api_codex"}, agents="codex")

    assert mcp_config.connected_integrations("claude-code")["github"] is True
    assert mcp_config.connected_integrations("claude-code")["linear"] is False
    assert mcp_config.connected_integrations("codex")["github"] is False
    assert mcp_config.connected_integrations("codex")["linear"] is True


def test_connected_integrations_reject_old_linear_stdio_config(tmp_path, monkeypatch):
    codex_config = tmp_path / "codex-config.toml"
    monkeypatch.setattr(mcp_config, "CODEX_CONFIG", codex_config)

    codex_config.write_text(
        '[mcp_servers.linear]\n'
        'command = "npx"\n'
        'args = ["-y", "@linear/mcp"]\n'
        '\n'
        '[mcp_servers.linear.env]\n'
        'LINEAR_API_KEY = "lin_api_old"\n',
        encoding="utf-8",
    )

    assert mcp_config.connected_integrations("codex")["linear"] is False


def test_sync_from_env_populates_linear_remote_codex_config(tmp_path, monkeypatch):
    codex_config = tmp_path / "codex-config.toml"
    monkeypatch.setattr(mcp_config, "CODEX_CONFIG", codex_config)

    mcp_config.sync_from_env("codex", {"LINEAR_API_KEY": "lin_api_synced"})

    codex_text = codex_config.read_text(encoding="utf-8")
    assert "[mcp_servers.linear]" in codex_text
    assert 'url = "https://mcp.linear.app/mcp"' in codex_text
    assert 'bearer_token_env_var = "LINEAR_API_KEY"' in codex_text


def test_add_server_replaces_existing_codex_env_block_without_duplicates(tmp_path, monkeypatch):
    codex_config = tmp_path / "codex-config.toml"
    monkeypatch.setattr(mcp_config, "CODEX_CONFIG", codex_config)

    mcp_config.add_server("github", {"api_key": "first"}, agents="codex")
    mcp_config.add_server("github", {"api_key": "second"}, agents="codex")

    codex_text = codex_config.read_text(encoding="utf-8")
    assert codex_text.count("[mcp_servers.github.env]") == 1
    assert 'GITHUB_PERSONAL_ACCESS_TOKEN = "second"' in codex_text
