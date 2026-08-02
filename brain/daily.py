"""Daily note generation."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def _plain_task_text(text: str) -> str:
    """Strip markdown formatting and task prefix to get comparable plain text."""
    t = re.sub(r'!\[\[[^\]]+\]\]', '', text)
    t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)
    t = re.sub(r'`([^`]+)`', r'\1', t)
    t = re.sub(r'\*([^*]+)\*', r'\1', t)
    t = re.sub(r'^-\s+\[[x ]\]\s+', '', t.strip())
    return ' '.join(t.split())

from brain.models import AppConfig, DailyContext, EnvConfig
from brain.vault import resolve_vault_paths, write_text_file


def daily_note_exists_for_today(app_cfg: AppConfig) -> bool:
    vault_paths = resolve_vault_paths(app_cfg)
    return (vault_paths.daily / f"{_today()}.md").exists()


def generate_daily_note(
    app_cfg: AppConfig,
    env_cfg: EnvConfig,
    *,
    force: bool = False,
    enabled_integrations: set[str] | None = None,
) -> Path:
    checked = _collect_checked_tasks(app_cfg)
    custom = _extract_custom_sections(app_cfg)
    context = build_daily_context(app_cfg, env_cfg, enabled_integrations=enabled_integrations)
    content = render_daily_note(context, enabled_integrations=enabled_integrations)
    if checked:
        content = _reapply_checked(content, checked)
    if custom:
        # Insert custom sections before the trailing --- footer
        footer_marker = "\n\n---"
        if footer_marker in content:
            content = content.replace(footer_marker, f"\n\n{custom}{footer_marker}", 1)
        else:
            content += f"\n\n{custom}"
    return write_daily_note(app_cfg, content, force=force)


# Headers that regenerate always re-renders from integrations — everything else is custom
_GENERATED_HEADERS = {
    "## Open Obsidian Tasks",
    "## Calendar — Today's Events",
    "## Email — Action Items",
    "## Notion Tasks",
    "## GitHub",
    "## Slack",
    "## Carried Forward",
    "## Reading — Today's Links",
}


def _extract_custom_sections(app_cfg: AppConfig) -> str:
    """Return any manually-added sections from today's note so regenerate preserves them."""
    vault_paths = resolve_vault_paths(app_cfg)
    daily_path = vault_paths.daily / f"{_today()}.md"
    if not daily_path.exists():
        return ""

    lines = daily_path.read_text(encoding="utf-8").splitlines()
    custom_blocks: list[list[str]] = []
    current: list[str] = []
    in_custom = False
    in_frontmatter = False
    frontmatter_done = False

    for line in lines:
        # Skip YAML frontmatter
        if not frontmatter_done:
            if line.strip() == "---":
                if not in_frontmatter:
                    in_frontmatter = True
                    continue
                else:
                    in_frontmatter = False
                    frontmatter_done = True
                    continue
            if in_frontmatter:
                continue

        if line.startswith("## "):
            if in_custom and current:
                custom_blocks.append(current)
            in_custom = line.strip() not in _GENERATED_HEADERS
            current = [line] if in_custom else []
        elif line.startswith("# ") or (line.strip() == "---"):
            if in_custom and current:
                custom_blocks.append(current)
            in_custom = False
            current = []
        elif in_custom:
            current.append(line)

    if in_custom and current:
        custom_blocks.append(current)

    if not custom_blocks:
        return ""

    parts = []
    for block in custom_blocks:
        # Drop trailing blank lines from each block
        while block and not block[-1].strip():
            block.pop()
        if block:
            parts.append("\n".join(block))

    return "\n\n".join(parts)


def _collect_checked_tasks(app_cfg: AppConfig) -> set[str]:
    """Return normalised plain-text of tasks already ticked in today's note."""
    vault_paths = resolve_vault_paths(app_cfg)
    daily_path = vault_paths.daily / f"{_today()}.md"
    if not daily_path.exists():
        return set()
    checked: set[str] = set()
    for line in daily_path.read_text(encoding="utf-8").splitlines():
        if "- [x] " in line:
            checked.add(_plain_task_text(line))
    return checked


def _reapply_checked(content: str, checked: set[str]) -> str:
    """Re-tick tasks in regenerated content that were checked in the old note."""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if "- [ ] " in line and _plain_task_text(line) in checked:
            lines[i] = line.replace("- [ ] ", "- [x] ", 1)
    return "\n".join(lines)


def render_daily_note(bundle: DailyContext, enabled_integrations: set[str] | None = None) -> str:
    def enabled(key: str) -> bool:
        return enabled_integrations is None or key in enabled_integrations

    sources = (
        (["obsidian"] if bundle.vault_notes and enabled("obsidian") else [])
        + (["calendar"] if bundle.calendar_events and enabled("calendar") else [])
        + (["gmail"] if bundle.email_items and enabled("email") else [])
        + (["notion"] if bundle.notion_tasks and enabled("notion") else [])
        + (["github"] if bundle.github_items and enabled("github") else [])
        + (["slack"] if bundle.slack_items and enabled("slack") else [])
        + (["news"] if bundle.reading_list else [])
    )

    day_label = datetime.now().strftime("%A, %B %d %Y")
    generated_at = datetime.now().strftime("%H:%M")

    lines = [
        "---",
        f"date: {bundle.today}",
        "type: daily",
        "generated: true",
        f"sources: [{', '.join(sources)}]",
        "---",
        "",
        f"# Daily Note — {day_label}",
        "",
    ]

    if enabled("obsidian"):
        lines += ["## Open Obsidian Tasks", ""]
        open_vault_tasks = [
            (note.relative_path, task["text"])
            for note in bundle.vault_notes
            for task in note.tasks
            if not task["done"]
        ]
        if open_vault_tasks:
            for relative_path, text in open_vault_tasks:
                lines.append(f"- [ ] {text} *(from: [[{Path(relative_path).stem}]])*")
        else:
            lines.append("*No open tasks in vault.*")
        lines.append("")

    if enabled("calendar"):
        lines += ["## Calendar — Today's Events", ""]
        if bundle.calendar_events:
            for event in bundle.calendar_events:
                if event["all_day"]:
                    lines.append(f"- [ ] All-day :: {event['title']}")
                else:
                    line = f"- [ ] {event['start']}–{event['end']} :: {event['title']}"
                    if event.get("location"):
                        line += f" @ {event['location']}"
                    lines.append(line)
        else:
            lines.append("*No events today.*")
        lines.append("")

    if enabled("email"):
        lines += ["## Email — Action Items", ""]
        if bundle.email_items:
            for email in bundle.email_items:
                lines.append(f"- [ ] {email['subject']} *(from: {email['from']})*")
        else:
            lines.append("*No unread emails in the last 24 hours.*")
        lines.append("")

    if enabled("notion"):
        lines += ["## Notion Tasks", ""]
        if bundle.notion_tasks:
            for task in bundle.notion_tasks:
                line = f"- [ ] {task['title']}"
                if task.get("due"):
                    line += f" · Due: {task['due']}"
                if task.get("url"):
                    line += f" · [Open]({task['url']})"
                lines.append(line)
        else:
            lines.append("*No open Notion tasks.*")
        lines.append("")

    if enabled("github"):
        lines += ["## GitHub", ""]
        if bundle.github_items:
            for item in bundle.github_items:
                label = "PR" if item["type"] == "pr" else "Issue"
                lines.append(f"- [ ] [{label}] [{item['title']}]({item['url']}) `{item['repo']}`")
        else:
            lines.append("*No open PRs or assigned issues.*")
        lines.append("")

    if enabled("slack"):
        lines += ["## Slack", ""]
        if bundle.slack_items:
            for item in bundle.slack_items:
                lines.append(f"- [ ] #{item['channel']}: {item['text']}")
        else:
            lines.append("*No recent Slack messages.*")
        lines.append("")

    if bundle.carry_forward:
        lines += ["## Carried Forward", ""]
        for item in bundle.carry_forward:
            lines.append(f"- [ ] {item['text']}")
        lines.append("")

    lines += ["## Reading — Today's Links", ""]
    if bundle.reading_list:
        for article in bundle.reading_list:
            source = f" *({article['source']})*" if article.get("source") else ""
            lines.append(f"- [{article['title']}]({article['url']}){source}")
    else:
        lines.append("*No articles fetched.*")

    lines += ["", "---", f"*Generated at {generated_at} by brain*"]
    return "\n".join(lines)


_CAPTURES_HEADER = "## Quick Captures"


def append_capture_task(app_cfg: AppConfig, env_cfg: EnvConfig, text: str, attachment_relpath: str) -> Path:
    """Append a screenshot-backed task to today's daily note, creating the note if needed."""
    vault_paths = resolve_vault_paths(app_cfg)
    daily_path = vault_paths.daily / f"{_today()}.md"
    if not daily_path.exists():
        generate_daily_note(app_cfg, env_cfg)

    task_line = f"- [ ] {text} ![[{attachment_relpath}]]"
    content = daily_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    if _CAPTURES_HEADER in lines:
        header_index = lines.index(_CAPTURES_HEADER)
        insert_at = header_index + 1
        while (
            insert_at < len(lines)
            and not lines[insert_at].startswith("## ")
            and not lines[insert_at].startswith("# ")
            and lines[insert_at].strip() != "---"
        ):
            insert_at += 1
        # Insert before the trailing blank line that separates this section from the next.
        while insert_at > header_index + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, task_line)
        content = "\n".join(lines)
    else:
        section = f"{_CAPTURES_HEADER}\n\n{task_line}"
        footer_marker = "\n\n---"
        if footer_marker in content:
            content = content.replace(footer_marker, f"\n\n{section}{footer_marker}", 1)
        else:
            content += f"\n\n{section}"

    return write_text_file(daily_path, content, overwrite=True)


def write_daily_note(app_cfg: AppConfig, content: str, *, force: bool = False) -> Path:
    vault_paths = resolve_vault_paths(app_cfg)
    daily_path = vault_paths.daily / f"{_today()}.md"
    if daily_path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing daily note: {daily_path}")
    return write_text_file(daily_path, content, overwrite=True)


def _today() -> str:
    return datetime.now().date().isoformat()


def build_daily_context(
    app_cfg: AppConfig,
    env_cfg: EnvConfig,
    enabled_integrations: set[str] | None = None,
) -> DailyContext:
    from brain.integration_context import build_daily_context as _build_daily_context

    return _build_daily_context(app_cfg, env_cfg, enabled_integrations=enabled_integrations)
