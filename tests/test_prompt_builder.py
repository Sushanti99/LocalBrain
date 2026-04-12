from datetime import datetime

from brain.app_config import default_app_config
from brain.models import SessionState, Turn
from brain.prompts import build_chat_prompt, build_codex_prompt
from brain.vault import resolve_vault_paths


def test_prompt_builder_includes_daily_and_core_context(tmp_path):
    app_cfg = default_app_config(tmp_path / "vault")
    vault_paths = resolve_vault_paths(app_cfg)
    for path in [vault_paths.daily, vault_paths.core, vault_paths.system]:
        path.mkdir(parents=True, exist_ok=True)
    (vault_paths.daily / "2026-04-11.md").write_text("# Today", encoding="utf-8")
    (vault_paths.core / "project.md").write_text("# Project", encoding="utf-8")
    (vault_paths.system / "CLAUDE.md").write_text("System instructions", encoding="utf-8")

    session = SessionState(
        session_id="2026-04-11-session-1",
        started_at=datetime.now().astimezone(),
        agent_name="claude-code",
        history=[Turn(role="user", content="Earlier question", created_at=datetime.now().astimezone())],
    )

    from brain import prompts as prompts_module

    class FakeDate:
        @staticmethod
        def today():
            return type("FakeDay", (), {"isoformat": lambda self: "2026-04-11"})()

    original_date = prompts_module.date
    prompts_module.date = FakeDate
    try:
        prompt = build_chat_prompt(app_cfg, session, "What should I do next?", vault_paths, inject_canonical_prompt=False)
    finally:
        prompts_module.date = original_date

    assert "Today's daily note exists" in prompt
    assert "project.md" in prompt
    assert "Earlier question" in prompt
    assert "System instructions" not in prompt


def test_codex_prompt_injects_canonical_prompt(tmp_path, monkeypatch):
    app_cfg = default_app_config(tmp_path / "vault", "codex")
    vault_paths = resolve_vault_paths(app_cfg)
    vault_paths.system.mkdir(parents=True, exist_ok=True)
    (vault_paths.system / "CLAUDE.md").write_text("Canonical prompt", encoding="utf-8")

    session = SessionState(
        session_id="2026-04-11-session-1",
        started_at=datetime.now().astimezone(),
        agent_name="codex",
    )

    monkeypatch.setattr("brain.prompts.date", type("FakeDate", (), {"today": staticmethod(lambda: type("D", (), {"isoformat": lambda self: "2026-04-11"})())}))
    prompt = build_codex_prompt(app_cfg, session, "Hello", vault_paths)

    assert "Canonical prompt" in prompt
