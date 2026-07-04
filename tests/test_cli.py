from brain import cli
from brain.oauth_flows import ProviderCredentials


def test_build_parser_accepts_connect_disconnect_integrations():
    parser = cli.build_parser()

    args = parser.parse_args(["connect", "linear", "--no-browser", "--port", "1234"])
    assert args.provider == "linear"
    assert args.no_browser is True
    assert args.port == 1234

    args = parser.parse_args(["disconnect", "linear"])
    assert args.provider == "linear"

    args = parser.parse_args(["integrations"])
    assert args.func is cli.cmd_integrations


def test_cmd_connect_unknown_provider_returns_1(capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["connect", "not-a-real-provider"])

    assert cli.cmd_connect(args) == 1
    assert "Unknown provider" in capsys.readouterr().err


def test_cmd_connect_uses_paste_key_fallback(monkeypatch, capsys):
    from brain import integrations_core

    stub_spec = integrations_core.ProviderSpec(
        id="stub", label="Stub Provider", oauth_configured=lambda: False,
        build_auth_url=None, exchange_code=None,
        credentials_from_key=lambda key: ProviderCredentials(
            integration_id="stub", mcp_credentials={"api_key": key}, env_updates={"STUB_KEY": key},
        ),
        key_prompt="Paste your Stub key: ",
    )
    monkeypatch.setitem(integrations_core.PROVIDERS, "stub", stub_spec)
    monkeypatch.setattr("builtins.input", lambda prompt="": "stub-secret")
    applied = {}
    monkeypatch.setattr(integrations_core, "apply_credentials", lambda creds, agents=None: applied.update(env=creds.env_updates))

    parser = cli.build_parser()
    args = parser.parse_args(["connect", "stub"])

    assert cli.cmd_connect(args) == 0
    assert applied["env"] == {"STUB_KEY": "stub-secret"}
    assert "connected" in capsys.readouterr().out


def test_cmd_connect_reports_validation_error_from_paste_key(monkeypatch, capsys):
    from brain import integrations_core

    stub_spec = integrations_core.ProviderSpec(
        id="stub", label="Stub Provider", oauth_configured=lambda: False,
        build_auth_url=None, exchange_code=None,
        credentials_from_key=lambda key: "That doesn't look right.",
        key_prompt="Paste your Stub key: ",
    )
    monkeypatch.setitem(integrations_core.PROVIDERS, "stub", stub_spec)
    monkeypatch.setattr("builtins.input", lambda prompt="": "bad-key")

    parser = cli.build_parser()
    args = parser.parse_args(["connect", "stub"])

    assert cli.cmd_connect(args) == 1
    assert "doesn't look right" in capsys.readouterr().err


def test_cmd_disconnect_unknown_provider_returns_1(capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["disconnect", "not-a-real-provider"])

    assert cli.cmd_disconnect(args) == 1
    assert "Unknown provider" in capsys.readouterr().err


def test_cmd_disconnect_calls_core(monkeypatch, capsys):
    from brain import integrations_core

    monkeypatch.setitem(integrations_core.PROVIDERS, "linear", integrations_core.PROVIDERS["linear"])
    called = {}
    monkeypatch.setattr(integrations_core, "disconnect_integration", lambda provider, agents=None: called.update(provider=provider))

    parser = cli.build_parser()
    args = parser.parse_args(["disconnect", "linear"])

    assert cli.cmd_disconnect(args) == 0
    assert called["provider"] == "linear"


def test_cmd_integrations_prints_status(monkeypatch, capsys):
    from brain import integrations_core

    monkeypatch.setattr(integrations_core, "compute_status", lambda agent=None: {"linear": True, "github": False})

    parser = cli.build_parser()
    args = parser.parse_args(["integrations"])

    assert cli.cmd_integrations(args) == 0
    out = capsys.readouterr().out
    assert "linear" in out and "connected" in out
    assert "github" in out and "not connected" in out
