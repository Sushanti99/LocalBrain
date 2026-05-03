"""Prompt construction."""

from __future__ import annotations

from datetime import date

from brain.models import AppConfig, DailyContext, SessionState, VaultPaths
from brain.vault import list_core_notes, read_daily_note


def _format_history_turn(turn) -> str:
    speaker = turn.role.upper()
    if turn.agent_name:
        speaker = f"{speaker} ({turn.agent_name})"
    return f"{speaker}: {turn.content}"


def load_canonical_prompt(vault_paths: VaultPaths) -> str:
    prompt_path = vault_paths.system / "CLAUDE.md"
    if not prompt_path.exists():
        return ""
    return prompt_path.read_text(encoding="utf-8").strip()


def build_chat_prompt(
    app_cfg: AppConfig,
    session_state: SessionState,
    user_message: str,
    vault_paths: VaultPaths,
    integration_digest: DailyContext | None = None,
    *,
    inject_canonical_prompt: bool,
    live_integration_context: str | None = None,
) -> str:
    today = date.today().isoformat()
    daily_content = read_daily_note(vault_paths, today)
    core_notes = list_core_notes(vault_paths)
    history = session_state.history[-app_cfg.session.history_turn_limit :]

    sections: list[str] = []
    if inject_canonical_prompt:
        canonical_prompt = load_canonical_prompt(vault_paths)
        if canonical_prompt:
            sections.append("## Operating Instructions")
            sections.append(canonical_prompt)

    sections.append("## Current Date")
    sections.append(today)

    sections.append("## Vault Context")
    if daily_content:
        sections.append(f"Today's daily note exists at {app_cfg.vault.daily_folder}/{today}.md")
        sections.append(daily_content[:4000])
    else:
        sections.append(
            f"Today's daily note does not yet exist. If needed, create {app_cfg.vault.daily_folder}/{today}.md."
        )

    core_names = [note.relative_path for note in core_notes]
    sections.append("Core notes:")
    if core_names:
        sections.extend(f"- {name}" for name in core_names[:50])
    else:
        sections.append("- none")
    sections.append(f"Thought summaries are archival in {app_cfg.vault.thoughts_folder}/.")

    sections.append("## Recent Session History")
    if history:
        for turn in history:
            sections.append(_format_history_turn(turn))
    else:
        sections.append("No prior turns in this session.")

    if app_cfg.integrations.include_in_prompt and integration_digest is not None:
        sections.append("## Integration Digest")
        sections.append(
            f"Calendar items: {len(integration_digest.calendar_events)} | "
            f"Unread emails: {len(integration_digest.email_items)} | "
            f"Open Notion tasks: {len(integration_digest.notion_tasks)}"
        )

    if live_integration_context:
        sections.append("## Live Integration Data")
        sections.append(live_integration_context)

    sections.append("## Current User Message")
    sections.append(user_message)
    return "\n\n".join(sections)


def build_codex_prompt(
    app_cfg: AppConfig,
    session_state: SessionState,
    user_message: str,
    vault_paths: VaultPaths,
    integration_digest: DailyContext | None = None,
    live_integration_context: str | None = None,
) -> str:
    return build_chat_prompt(
        app_cfg,
        session_state,
        user_message,
        vault_paths,
        integration_digest,
        inject_canonical_prompt=True,
        live_integration_context=live_integration_context,
    )
