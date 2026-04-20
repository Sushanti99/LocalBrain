"""Prompt construction for incremental vault ingest."""

from __future__ import annotations

from brain.models import VaultPaths


def build_ingest_prompt(vault_paths: VaultPaths, integration_id: str) -> str:
    ingest_file = f"{vault_paths.system.name}/_ingest_{integration_id}.md"
    core_rel = vault_paths.core.name
    refs_rel = vault_paths.references.name

    return f"""New data has arrived from {integration_id}.

Read {ingest_file} — a fresh snapshot from {integration_id}.
Read the existing notes in {core_rel}/ and {refs_rel}/.

Your job: make minimal, surgical updates to existing notes where this data adds useful context. You are maintaining a wiki — find the right page, add a concise phrase, or update a stale fact. The vault should feel like it was always up to date, not like data was imported.

Rules:
- Prefer updating an existing sentence over adding a new one
- Total edits across the entire vault: 3 lines maximum
- Do not create new notes unless this data introduces a concept with genuinely no home anywhere in the vault
- If nothing is worth adding, change nothing
- When done, delete {ingest_file}
"""
