"""Daily-note integration context collection."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from brain.models import AppConfig, DailyContext, EnvConfig
from brain.vault import read_vault


def fetch_tagged_integration_data(
    tags: list[str],
    query: str,
    app_cfg: AppConfig,
    env_cfg: EnvConfig,
) -> str:
    """Fetch live data for @-tagged integrations and return a formatted context string."""
    _ensure_project_root_on_path()
    legacy_config = _load_legacy_module("config")
    _configure_legacy_modules(legacy_config, app_cfg, env_cfg)

    sections: list[str] = []
    for tag in tags:
        if tag in ("gmail", "email"):
            sections.append(_fetch_gmail_context())
        elif tag == "calendar":
            sections.append(_fetch_calendar_context())
        elif tag == "github":
            if token := os.getenv("GITHUB_TOKEN"):
                items = _fetch_github_items(token)
                if items:
                    lines = [f"- [{i['type'].upper()}] {i['title']} ({i['repo']})" for i in items]
                    sections.append("### GitHub\n" + "\n".join(lines))
        elif tag == "slack":
            if token := os.getenv("SLACK_BOT_TOKEN"):
                items = _fetch_slack_items(token)
                if items:
                    lines = [f"- #{i['channel']}: {i['text']}" for i in items]
                    sections.append("### Slack\n" + "\n".join(lines))
        elif tag == "notion":
            try:
                notion_client = _load_legacy_module("notion_client")
                items = notion_client.get_open_tasks()
                if items:
                    lines = [f"- {t.get('title', '')}" for t in items[:20]]
                    sections.append("### Notion Tasks\n" + "\n".join(lines))
            except Exception:
                pass

    return "\n\n".join(s for s in sections if s)


def _fetch_gmail_context() -> str:
    try:
        gmail_client = _load_legacy_module("gmail_client")
        items = gmail_client.get_action_items(max_results=50)
        if not items:
            return "### Gmail\nNo recent emails found."
        import json
        return f"### Gmail (recent emails)\n{json.dumps(items, indent=2)}"
    except Exception as exc:
        return f"### Gmail\nError fetching emails: {exc}"


def _fetch_calendar_context() -> str:
    try:
        calendar_client = _load_legacy_module("calendar_client")
        events = calendar_client.get_todays_events()
        if not events:
            return "### Calendar\nNo events today."
        import json
        return f"### Calendar (today's events)\n{json.dumps(events, indent=2)}"
    except Exception as exc:
        return f"### Calendar\nError fetching events: {exc}"


def build_daily_context(
    app_cfg: AppConfig,
    env_cfg: EnvConfig,
    enabled_integrations: set[str] | None = None,
) -> DailyContext:
    def want(key: str) -> bool:
        return enabled_integrations is None or key in enabled_integrations

    _ensure_project_root_on_path()
    legacy_config = _load_legacy_module("config")
    _configure_legacy_modules(legacy_config, app_cfg, env_cfg)
    bundle = DailyContext(today=date.today().isoformat())

    from brain.vault import resolve_vault_paths
    vault_paths = resolve_vault_paths(app_cfg)
    dismissed = _load_dismissed_from_yesterday(vault_paths.daily)

    if want("obsidian"):
        try:
            all_notes = read_vault(app_cfg.vault.path)
            bundle.vault_notes = [note for note in all_notes if not note.frontmatter.get("generated")]
        except Exception:
            bundle.vault_notes = []

    if want("calendar"):
        try:
            calendar_client = _load_legacy_module("calendar_client")
            bundle.calendar_events = calendar_client.get_todays_events()
        except Exception:
            bundle.calendar_events = []

    if want("email"):
        try:
            gmail_client = _load_legacy_module("gmail_client")
            items = gmail_client.get_action_items()
            bundle.email_items = [e for e in items if not _is_dismissed(e.get("subject", ""), dismissed)]
        except Exception:
            bundle.email_items = []

    if want("notion"):
        try:
            notion_client = _load_legacy_module("notion_client")
            items = notion_client.get_open_tasks()
            bundle.notion_tasks = [t for t in items if not _is_dismissed(t.get("title", ""), dismissed)]
        except Exception:
            bundle.notion_tasks = []

    if want("github"):
        if token := os.getenv("GITHUB_TOKEN"):
            items = _fetch_github_items(token)
            bundle.github_items = [i for i in items if not _is_dismissed(i.get("title", ""), dismissed)]

    if want("slack"):
        if token := os.getenv("SLACK_BOT_TOKEN"):
            bundle.slack_items = _fetch_slack_items(token)

    try:
        news_client = _load_legacy_module("news_client")
        bundle.reading_list = news_client.get_reading_list(bundle.vault_notes)
    except Exception:
        bundle.reading_list = []

    bundle.carry_forward = _load_carry_forward(vault_paths.daily, dismissed)

    return bundle


def _load_carry_forward(daily_folder: Path, dismissed: set[str]) -> list[dict]:
    """Unchecked items from yesterday's note, skipping calendar and reading sections."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    yesterday_path = daily_folder / f"{yesterday}.md"
    if not yesterday_path.exists():
        return []
    skip = {"Reading — Today's Links", "Calendar — Today's Events"}
    items = []
    current_section = None
    for line in yesterday_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current_section = line[3:].strip()
        elif current_section and current_section not in skip and line.startswith("- [ ] "):
            text = line[6:].strip()
            if text and text not in dismissed:
                items.append({"section": current_section, "text": text})
    return items


def _load_dismissed_from_yesterday(daily_folder: Path) -> set[str]:
    """Return set of task text snippets that were ticked [x] in yesterday's daily note."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    yesterday_path = daily_folder / f"{yesterday}.md"
    if not yesterday_path.exists():
        return set()
    dismissed: set[str] = set()
    for line in yesterday_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- [x] "):
            dismissed.add(line[6:].strip())
    return dismissed


def _is_dismissed(text: str, dismissed: set[str]) -> bool:
    if not text or not dismissed:
        return False
    for item in dismissed:
        if text in item or item.startswith(text[:40]):
            return True
    return False


def _fetch_github_items(token: str) -> list[dict]:
    try:
        import httpx
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        items = []
        prs = httpx.get("https://api.github.com/search/issues?q=is:pr+is:open+author:@me&per_page=10", headers=headers, timeout=10).json()
        for i in (prs.get("items") or []):
            items.append({"type": "pr", "title": i["title"], "url": i["html_url"], "repo": i["repository_url"].split("/")[-1]})
        issues = httpx.get("https://api.github.com/search/issues?q=is:issue+is:open+assignee:@me&per_page=10", headers=headers, timeout=10).json()
        for i in (issues.get("items") or []):
            items.append({"type": "issue", "title": i["title"], "url": i["html_url"], "repo": i["repository_url"].split("/")[-1]})
        return items
    except Exception:
        return []


def _fetch_slack_items(token: str) -> list[dict]:
    try:
        import httpx
        headers = {"Authorization": f"Bearer {token}"}
        channels = httpx.get("https://slack.com/api/conversations.list?limit=10&exclude_archived=true", headers=headers, timeout=10).json()
        items = []
        for ch in (channels.get("channels") or [])[:5]:
            hist = httpx.get(f"https://slack.com/api/conversations.history?channel={ch['id']}&limit=3", headers=headers, timeout=10).json()
            for msg in (hist.get("messages") or []):
                text = msg.get("text", "").strip()
                if text:
                    items.append({"channel": ch["name"], "text": text[:140]})
        return items
    except Exception:
        return []


def _configure_legacy_modules(legacy_config, app_cfg: AppConfig, env_cfg: EnvConfig) -> None:
    legacy_config.VAULT_PATH = app_cfg.vault.path
    legacy_config.DAILY_FOLDER = app_cfg.vault.daily_folder
    legacy_config.GOOGLE_CREDENTIALS_FILE = env_cfg.google_credentials_file
    legacy_config.GOOGLE_TOKEN_FILE = env_cfg.google_token_file
    legacy_config.NOTION_API_KEY = env_cfg.notion_api_key
    legacy_config.NEWS_FEEDS = ",".join(env_cfg.news_feeds)


def _load_legacy_module(module_name: str):
    if module_name in sys.modules:
        return sys.modules[module_name]

    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        module_path = _legacy_module_path(module_name)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module


def _legacy_module_path(module_name: str) -> Path:
    project_root = Path(__file__).resolve().parent.parent
    return project_root / f"{module_name}.py"


def _ensure_project_root_on_path() -> None:
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
