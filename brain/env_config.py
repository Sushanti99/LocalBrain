"""Environment-backed integration configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from brain.models import EnvConfig


def user_app_support_dir() -> Path:
    """Per-user data dir for the Mac app.

    BRAIN_USER_ID (set by the Mac app from the signed-in Supabase user) namespaces
    integration tokens per-user so signing in as a different account on the same
    Mac doesn't surface the previous user's connections. Falls back to the shared
    dir when running from CLI or in dev where no user ID is set.
    """
    base = Path.home() / "Library" / "Application Support" / "BrainSquared"
    user_id = os.getenv("BRAIN_USER_ID", "").strip()
    target = base / "users" / user_id if user_id else base
    target.mkdir(parents=True, exist_ok=True)
    return target


def _find_dotenv() -> Path | None:
    """Search project root, then per-user App Support, then CWD upward for a .env file."""
    project_root = Path(__file__).resolve().parent.parent
    candidate = project_root / ".env"
    if candidate.exists():
        return candidate
    user_env = user_app_support_dir() / ".env"
    if user_env.exists():
        return user_env
    here = Path.cwd()
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def resolve_env_file() -> Path:
    """Return the .env file to read from — and the one new credentials get written to.

    Reuses the same search order as `_find_dotenv()`; if nothing exists yet, falls
    back to the project root (if writable) or the per-user App Support dir.
    """
    existing = _find_dotenv()
    if existing is not None:
        return existing
    project_root = Path(__file__).resolve().parent.parent
    if os.access(project_root, os.W_OK):
        return project_root / ".env"
    return user_app_support_dir() / ".env"


def update_env(key: str, value: str, *, env_file: Path | None = None) -> None:
    """Set KEY=value in the resolved .env file (and the current process env)."""
    target = env_file if env_file is not None else resolve_env_file()
    lines: list[str] = []
    found = False
    if target.exists():
        for line in target.read_text().splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def remove_env(key: str, *, env_file: Path | None = None) -> None:
    """Remove KEY from the resolved .env file (and the current process env)."""
    target = env_file if env_file is not None else resolve_env_file()
    if target.exists():
        lines = [l for l in target.read_text().splitlines() if not l.strip().startswith(f"{key}=")]
        target.write_text("\n".join(lines) + "\n")
    os.environ.pop(key, None)


def load_env_config(env_file: str | Path | None = None) -> EnvConfig:
    if env_file is None:
        dotenv_path = _find_dotenv()
        load_dotenv(dotenv_path)
    else:
        dotenv_path = Path(env_file)
        load_dotenv(dotenv_path)

    base = dotenv_path.parent if dotenv_path else user_app_support_dir()

    def _resolve(value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (base / p).resolve()

    google_credentials = _resolve(os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"))
    google_token = _resolve(os.getenv("GOOGLE_TOKEN_FILE", "token.json"))
    notion_api_key = os.getenv("NOTION_API_KEY", "")
    news_feeds = [item.strip() for item in os.getenv("NEWS_FEEDS", "").split(",") if item.strip()]

    raw_env = {
        "GOOGLE_CREDENTIALS_FILE": str(google_credentials),
        "GOOGLE_TOKEN_FILE": str(google_token),
        "NOTION_API_KEY": notion_api_key,
        "NEWS_FEEDS": ",".join(news_feeds),
    }

    return EnvConfig(
        google_credentials_file=google_credentials,
        google_token_file=google_token,
        notion_api_key=notion_api_key,
        news_feeds=news_feeds,
        raw_env=raw_env,
    )


def integration_status(env_cfg: EnvConfig) -> dict[str, bool]:
    return {
        "google": env_cfg.google_token_file.exists() or env_cfg.google_credentials_file.exists(),
        "notion": bool(env_cfg.notion_api_key),
        "news": bool(env_cfg.news_feeds),
    }
