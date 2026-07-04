from brain import env_config


def test_update_env_appends_new_key(tmp_path):
    env_file = tmp_path / ".env"
    env_config.update_env("FOO", "bar", env_file=env_file)

    assert env_file.read_text() == "FOO=bar\n"
    assert __import__("os").environ["FOO"] == "bar"


def test_update_env_replaces_existing_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=old\nOTHER=keep\n")

    env_config.update_env("FOO", "new", env_file=env_file)

    text = env_file.read_text()
    assert "FOO=new" in text
    assert "OTHER=keep" in text
    assert "FOO=old" not in text


def test_remove_env_deletes_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\nOTHER=keep\n")

    env_config.remove_env("FOO", env_file=env_file)

    text = env_file.read_text()
    assert "FOO=bar" not in text
    assert "OTHER=keep" in text


def test_remove_env_on_missing_file_is_a_noop(tmp_path):
    env_file = tmp_path / "does-not-exist.env"
    env_config.remove_env("FOO", env_file=env_file)  # should not raise


def test_resolve_env_file_prefers_existing_project_env(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".env").write_text("EXISTING=1\n")
    monkeypatch.setattr(env_config, "_find_dotenv", lambda: project_root / ".env")

    assert env_config.resolve_env_file() == project_root / ".env"


def test_resolve_env_file_falls_back_to_user_app_support_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(env_config, "_find_dotenv", lambda: None)
    fallback_dir = tmp_path / "app-support"
    fallback_dir.mkdir()
    monkeypatch.setattr(env_config, "user_app_support_dir", lambda: fallback_dir)
    monkeypatch.setattr(env_config.os, "access", lambda *a, **k: False)

    assert env_config.resolve_env_file() == fallback_dir / ".env"
