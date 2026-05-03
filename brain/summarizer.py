"""Session summary generation and persistence."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from brain.models import SessionState
from brain.utils import format_duration_minutes, next_available_session_summary_path, utc_now
from brain.vault import write_text_file


def session_agent_label(session: SessionState) -> str:
    agents = {turn.agent_name for turn in session.history if turn.agent_name}
    if len(agents) == 1:
        return next(iter(agents))
    if len(agents) > 1:
        return "mixed"
    return session.agent_name


def _format_turn(turn) -> str:
    speaker = turn.role.upper()
    if turn.agent_name:
        speaker = f"{speaker} ({turn.agent_name})"
    return f"{speaker}: {turn.content}"


def build_summary_prompt(session: SessionState) -> str:
    history = "\n".join(_format_turn(turn) for turn in session.history[-20:])
    modified_files = "\n".join(f"- {path}" for path in sorted(session.modified_files)) or "- none"
    return (
        "Summarize this Brain session as concise markdown with the headings "
        "'## Topics Discussed', '## Decisions Made', '## Files Modified', and '## Action Items'.\n\n"
        f"Session ID: {session.session_id}\n\n"
        f"Modified files:\n{modified_files}\n\n"
        f"Conversation:\n{history}"
    )


def fallback_summary(session: SessionState) -> str:
    user_messages = [turn.content for turn in session.history if turn.role == "user"]
    assistant_messages = [turn.content for turn in session.history if turn.role == "assistant"]
    modified_files = sorted(session.modified_files)
    topics = [f"- {msg[:160]}" for msg in user_messages[:5]] or ["- No user prompts captured."]
    decisions = [f"- {msg[:160]}" for msg in assistant_messages[:5]] or ["- No assistant responses captured."]
    files = [f"- {path}" for path in modified_files] or ["- No modified files detected."]
    return "\n".join(
        [
            "# Session Summary",
            "",
            "## Topics Discussed",
            *topics,
            "",
            "## Decisions Made",
            *decisions,
            "",
            "## Files Modified",
            *files,
            "",
            "## Action Items",
            "- Review the session summary and continue from the latest vault state.",
        ]
    )


async def write_session_summary(
    thoughts_dir: Path,
    session: SessionState,
    *,
    agent_summary_text: str | None = None,
) -> Path:
    thoughts_dir.mkdir(parents=True, exist_ok=True)
    ended_at = utc_now()
    output_path, session_number = next_available_session_summary_path(thoughts_dir, today=date.today())
    body = agent_summary_text.strip() if agent_summary_text else fallback_summary(session)
    content = "\n".join(
        [
            "---",
            f"date: {date.today().isoformat()}",
            f"session: {session_number}",
            f"agent: {session_agent_label(session)}",
            f"duration_minutes: {format_duration_minutes(session.started_at, ended_at)}",
            "---",
            "",
            body,
            "",
        ]
    )
    return write_text_file(output_path, content, overwrite=False)
