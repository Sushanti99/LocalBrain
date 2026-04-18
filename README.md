# brain² (BrainSquared)

Your second brain, on your laptop. No cloud. No subscriptions. Just your data and an AI that knows it.

You connect your existing tools — Obsidian, Gmail, Google Calendar, Notion — and brain² seeds a new vault from your real data. Then you chat with it through a minimal browser UI. The agent reads and writes your vault directly. Everything stays on your machine.

---

## How it works

```
Browser UI  ──►  brain² Server  ──►  Claude Code / Codex CLI
                                                │
                                         Obsidian Vault (markdown files)
                                                │
                                    Gmail · Calendar · Notion · RSS
```

- **Obsidian is the database.** No SQLite, no Redis — just markdown files.
- **The agent is swappable.** Claude Code or Codex, configured at startup.
- **The UI is minimal.** One HTML file, no build step, opens instantly.
- **Everything is local.** Your vault, your machine, your data.

---

## Quickstart

### 1. Install

```bash
git clone https://github.com/Sushanti/brainsquared
cd brainsquared
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
```

Requires [Claude Code](https://claude.ai/code) (or Codex) installed and authenticated.

### 2. Connect your tools (optional)

```bash
python bootstrap.py
```

Walks you through Google OAuth (Gmail + Calendar), Notion API key, and RSS feeds. Writes credentials to `.env`. All integrations are optional — brain² works with just a vault.

### 3. Seed a new vault

```bash
brain seed --vault ~/my-vault \
                --from-obsidian ~/path/to/existing-vault \
                --from-notion \
                --from-gmail \
                --from-calendar
```

brain² collects your existing data, runs it through Claude, and populates:

```
my-vault/
├── core/          ← profile, projects, interests, people
├── references/    ← reference material (if found)
├── daily/         ← today's note with tasks, events, emails
├── thoughts/      ← AI conversation summaries (auto-written)
└── system/        ← config and agent instructions
```

Use `--dry-run` to inspect collected data before the agent writes anything.

### 4. Start

```bash
brain start --vault ~/my-vault
```

Opens `http://localhost:3000`. Chat with your vault. Click the **home icon** next to the title to browse all your notes.

---

## All commands

```bash
brain seed    --vault PATH  [--from-obsidian PATH] [--from-notion] [--from-gmail] [--from-calendar] [--dry-run]
brain init    --vault PATH  [--agent claude-code|codex]
brain start   --vault PATH  [--agent claude-code|codex] [--port N] [--no-open]
brain daily   --vault PATH  [--force]
brain status  --vault PATH
```

---

## Integrations

| Integration | Used for | Required |
|---|---|---|
| Google Calendar | Daily note events, seed context | No |
| Gmail | Daily note action items, seed context | No |
| Notion | Open tasks, page content | No |
| RSS feeds | Reading list in daily note | No |
| Claude Code | Agent backend | Yes (or Codex) |

---

## Vault structure

brain² uses five folders. Existing vault folders are mapped automatically — your `Daily/` becomes `daily`, your `References/` becomes `references`, etc.

| Folder | Purpose |
|---|---|
| `core/` | Persistent notes: profile, projects, interests, people |
| `references/` | Reference material, links, resources |
| `daily/` | One note per day, generated from integrations |
| `thoughts/` | Auto-written summaries of AI conversations |
| `system/` | `brain.config.yaml` and `CLAUDE.md` agent instructions |

---

## Development

```bash
pip install -e '.[test]'
pytest -q
```

The integration clients (`gmail_client.py`, `calendar_client.py`, `notion_client.py`, `news_client.py`) live at the project root and are loaded dynamically at runtime. `config.py` is the central configuration module they all import from.

---

## Roadmap

- [ ] `brain setup` — guided OAuth app setup so each user owns their own Google credentials (avoid shared credential abuse)
- [ ] Move integration clients into `brain/integrations/`
- [ ] VPS deployment (Hetzner/Fly.io) with obsidian-headless sync
- [ ] Mobile access via Tailscale
- [ ] Background scheduled tasks
