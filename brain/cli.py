"""Brain command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from brain import __version__
from brain.agent_backends import get_backend
from brain.app_config import load_app_config
from brain.env_config import integration_status, load_env_config
from brain.init_vault import initialize_vault
from brain.models import DEFAULT_SERVER_PORT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="brain — local-first personal agent harness for an Obsidian vault")
    parser.add_argument("--version", action="version", version=f"brain {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize or convert a Brain-compatible vault")
    init_parser.add_argument("--vault", required=True, help="Vault path to create or convert")
    init_parser.add_argument("--agent", choices=["claude-code", "codex"], default="claude-code")
    init_parser.add_argument("--force-create-daily", action="store_true")
    init_parser.add_argument("--overwrite-system-files", action="store_true")
    init_parser.set_defaults(func=cmd_init)

    start_parser = subparsers.add_parser("start", help="Start the Brain local server")
    start_parser.add_argument("--vault", help="Vault path")
    start_parser.add_argument("--config", help="Explicit path to brain.config.yaml")
    start_parser.add_argument("--agent", choices=["claude-code", "codex"])
    start_parser.add_argument("--port", type=int)
    start_parser.add_argument("--no-open", action="store_true")
    start_parser.set_defaults(func=cmd_start)

    status_parser = subparsers.add_parser("status", help="Show vault, integration, and backend readiness")
    status_parser.add_argument("--vault", help="Vault path")
    status_parser.add_argument("--config", help="Explicit path to brain.config.yaml")
    status_parser.add_argument("--agent", choices=["claude-code", "codex"])
    status_parser.set_defaults(func=cmd_status)

    chat_parser = subparsers.add_parser("chat", help="Chat with your Brain agent in the terminal")
    chat_parser.add_argument("--vault", help="Vault path")
    chat_parser.add_argument("--config", help="Explicit path to brain.config.yaml")
    chat_parser.add_argument("--agent", choices=["claude-code", "codex"])
    chat_parser.set_defaults(func=cmd_chat)

    daily_parser = subparsers.add_parser("daily", help="Generate today's daily note")
    daily_parser.add_argument("--vault", help="Vault path")
    daily_parser.add_argument("--config", help="Explicit path to brain.config.yaml")
    daily_parser.add_argument("--force", action="store_true")
    daily_parser.set_defaults(func=cmd_daily)

    seed_parser = subparsers.add_parser(
        "seed",
        help="Create and populate a new Brain vault from your existing tools",
    )
    seed_parser.add_argument("--vault", required=True, help="Path for the new Brain vault")
    seed_parser.add_argument("--agent", choices=["claude-code", "codex"], default="claude-code")
    seed_parser.add_argument("--from-obsidian", metavar="PATH", help="Import notes from an existing Obsidian vault")
    seed_parser.add_argument("--from-notion", action="store_true", help="Import from Notion (requires NOTION_API_KEY)")
    seed_parser.add_argument("--from-gmail", action="store_true", help="Import context from Gmail (requires Google auth)")
    seed_parser.add_argument("--from-calendar", action="store_true", help="Import commitments from Google Calendar")
    seed_parser.add_argument("--dry-run", action="store_true", help="Collect data and write seed input but skip agent synthesis")
    seed_parser.set_defaults(func=cmd_seed)

    connect_parser = subparsers.add_parser("connect", help="Connect a third-party integration")
    connect_parser.add_argument("provider", help="google | github | slack | notion | linear")
    connect_parser.add_argument("--agent", choices=["claude-code", "codex"], help="Defaults to both backends")
    connect_parser.add_argument("--no-browser", action="store_true", help="Print the auth URL instead of opening a browser")
    connect_parser.add_argument("--port", type=int, default=DEFAULT_SERVER_PORT)
    connect_parser.set_defaults(func=cmd_connect)

    disconnect_parser = subparsers.add_parser("disconnect", help="Disconnect a third-party integration")
    disconnect_parser.add_argument("provider", help="google | github | slack | notion | linear")
    disconnect_parser.add_argument("--agent", choices=["claude-code", "codex"], help="Defaults to both backends")
    disconnect_parser.set_defaults(func=cmd_disconnect)

    integrations_parser = subparsers.add_parser("integrations", help="Show connected integration status")
    integrations_parser.add_argument("--agent", choices=["claude-code", "codex"])
    integrations_parser.set_defaults(func=cmd_integrations)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    result = initialize_vault(
        Path(args.vault).expanduser().resolve(),
        agent=args.agent,
        force_create_daily=args.force_create_daily,
        overwrite_system_files=args.overwrite_system_files,
    )
    print(f"Vault: {result.vault_path}")
    print("Created directories:")
    for path in result.created_paths or []:
        print(f"  - {path}")
    if not result.created_paths:
        print("  - none")
    print("Reused directories:")
    for path in result.reused_paths or []:
        print(f"  - {path}")
    if not result.reused_paths:
        print("  - none")
    print("Created files:")
    for path in result.created_files or []:
        print(f"  - {path}")
    if not result.created_files:
        print("  - none")
    print("Reused files:")
    for path in result.reused_files or []:
        print(f"  - {path}")
    if not result.reused_files:
        print("  - none")
    print("Folder mapping:")
    for key, value in result.folder_mappings.items():
        print(f"  - {key}: {value}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    from brain.server import run_server

    app_cfg = load_app_config(
        vault_path=args.vault,
        config_path=args.config,
        agent_override=args.agent,
        port_override=args.port,
    )
    backend = get_backend(app_cfg)
    validation = backend.validate_installation()
    if not validation.installed:
        raise RuntimeError(validation.error or f"Backend unavailable: {app_cfg.agent}")
    env_cfg = load_env_config()
    run_server(app_cfg, env_cfg, open_browser=not args.no_open)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    app_cfg = load_app_config(
        vault_path=args.vault,
        config_path=args.config,
        agent_override=args.agent,
    )
    env_cfg = load_env_config()
    backend = get_backend(app_cfg)
    validation = backend.validate_installation()
    integrations = integration_status(env_cfg)

    print(f"Vault path: {app_cfg.vault.path}")
    print(f"Configured agent: {app_cfg.agent}")
    print(f"Agent binary path: {validation.resolved_path or 'missing'}")
    print(f"Agent version: {validation.version or 'unknown'}")
    print(f"Server port: {app_cfg.server.port}")
    print("Folder mapping:")
    print(f"  daily: {app_cfg.vault.daily_folder}")
    print(f"  core: {app_cfg.vault.core_folder}")
    print(f"  references: {app_cfg.vault.references_folder}")
    print(f"  thoughts: {app_cfg.vault.thoughts_folder}")
    print(f"  system: {app_cfg.vault.system_folder}")
    print("Integrations:")
    print(f"  Google credentials: {'present' if integrations['google'] else 'missing'}")
    print(f"  Notion: {'configured' if integrations['notion'] else 'missing'}")
    print(f"  News feeds: {'configured' if integrations['news'] else 'default-only'}")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    import asyncio

    from brain import mcp_config
    from brain.prompts import build_chat_prompt, build_codex_prompt
    from brain.server import _build_backend_env
    from brain.session import SessionManager
    from brain.summarizer import build_summary_prompt, fallback_summary, write_session_summary
    from brain.vault import diff_modified_files, resolve_vault_paths, snapshot_vault_mtimes

    app_cfg = load_app_config(vault_path=args.vault, config_path=args.config, agent_override=args.agent)
    env_cfg = load_env_config()
    agent_name = app_cfg.agent

    backend = get_backend(app_cfg, agent_name)
    validation = backend.validate_installation()
    if not validation.installed:
        print(validation.error or f"Backend unavailable: {agent_name}", file=sys.stderr)
        return 1

    mcp_config.sync_from_env(agent_name)
    session_manager = SessionManager(agent_name)
    vault_paths = resolve_vault_paths(app_cfg)

    print(f"brain chat — agent: {agent_name}, vault: {app_cfg.vault.path}")
    print("Type a message, 'end' to summarize and close the session, or 'exit'/Ctrl+D to quit.\n")

    async def end_session() -> None:
        session = session_manager.current_session()
        if session is None or not session.history:
            print("No conversation to summarize yet.")
            return
        session_manager.mark_summarizing()
        print("Summarizing session...")
        try:
            summary_prompt = build_summary_prompt(session)
            summary_text = await backend.summarize(summary_prompt, app_cfg.vault.path, _build_backend_env(env_cfg))
        except Exception:
            summary_text = fallback_summary(session)
        summary_path = await write_session_summary(vault_paths.thoughts, session, agent_summary_text=summary_text)
        session_manager.close_session()
        print(f"Session summary written to {summary_path}")

    async def run() -> None:
        while True:
            try:
                user_message = input("you> ").strip()
            except EOFError:
                print()
                return
            if not user_message:
                continue
            if user_message.lower() in ("exit", "quit"):
                return
            if user_message.lower() == "end":
                await end_session()
                return

            session = session_manager.get_or_create_session()
            session_manager.add_turn("user", user_message, agent_name=agent_name)
            prompt = (
                build_chat_prompt(app_cfg, session, user_message, vault_paths, inject_canonical_prompt=False, env_cfg=env_cfg)
                if agent_name == "claude-code"
                else build_codex_prompt(app_cfg, session, user_message, vault_paths)
            )

            before = snapshot_vault_mtimes(app_cfg.vault.path)
            assistant_chunks: list[str] = []
            print("brain> ", end="", flush=True)
            try:
                async for event in backend.stream(prompt, app_cfg.vault.path, _build_backend_env(env_cfg)):
                    if event.type == "chunk" and event.content:
                        assistant_chunks.append(event.content)
                        print(event.content, end="", flush=True)
                    elif event.type == "tool_use" and event.content:
                        print(f"\n[tool: {event.content}]", flush=True)
                    elif event.type == "error":
                        print(f"\n[error] {event.content}")
            except Exception as exc:
                print(f"\n[error] {exc}")
            print("\n")

            after = snapshot_vault_mtimes(app_cfg.vault.path)
            session_manager.finish_run("".join(assistant_chunks), diff_modified_files(before, after), agent_name=agent_name)

    asyncio.run(run())
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    from brain.daily import generate_daily_note

    app_cfg = load_app_config(vault_path=args.vault, config_path=args.config)
    env_cfg = load_env_config()
    path = generate_daily_note(app_cfg, env_cfg, force=args.force)
    print(path)
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    from brain.seeder import SeedSources, run_seed

    sources = SeedSources(
        from_obsidian=Path(args.from_obsidian).expanduser() if args.from_obsidian else None,
        from_notion=args.from_notion,
        from_gmail=args.from_gmail,
        from_calendar=args.from_calendar,
    )
    result = run_seed(
        vault_path=Path(args.vault),
        agent=args.agent,
        sources=sources,
        dry_run=args.dry_run,
    )
    if result.sources_used:
        print(f"\nVault seeded at: {result.vault_path}")
        print(f"Sources used: {', '.join(result.sources_used)}")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    from brain import integrations_core

    spec = integrations_core.PROVIDERS.get(args.provider)
    if spec is None:
        print(f"Unknown provider {args.provider!r}. Supported: {', '.join(integrations_core.PROVIDERS)}", file=sys.stderr)
        return 1

    try:
        if spec.build_auth_url is not None and spec.oauth_configured():
            creds = integrations_core.connect_via_browser(spec, port=args.port, open_browser=not args.no_browser)
        elif spec.credentials_from_key is not None:
            key = input(spec.key_prompt or f"Paste your {spec.label} API key: ").strip()
            result = spec.credentials_from_key(key)
            if isinstance(result, str):
                print(result, file=sys.stderr)
                return 1
            creds = result
        else:
            print(f"{spec.label} requires OAuth client credentials — see .env.example.", file=sys.stderr)
            return 1
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    integrations_core.apply_credentials(creds, agents=args.agent)
    print(f"{spec.label} connected.")
    return 0


def cmd_disconnect(args: argparse.Namespace) -> int:
    from brain import integrations_core

    if args.provider not in integrations_core.PROVIDERS:
        print(f"Unknown provider {args.provider!r}. Supported: {', '.join(integrations_core.PROVIDERS)}", file=sys.stderr)
        return 1
    integrations_core.disconnect_integration(args.provider, agents=args.agent)
    print(f"{args.provider} disconnected.")
    return 0


def cmd_integrations(args: argparse.Namespace) -> int:
    from brain import integrations_core

    status = integrations_core.compute_status(args.agent)
    for name, connected in status.items():
        print(f"  {name:10s} {'connected' if connected else 'not connected'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env_config()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
