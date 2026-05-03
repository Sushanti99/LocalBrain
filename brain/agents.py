"""Shared runtime agent registry helpers."""

from __future__ import annotations

from dataclasses import dataclass

from brain.models import AgentName, AppConfig


@dataclass(frozen=True, slots=True)
class AgentOption:
    id: AgentName
    label: str
    implemented: bool = True


AGENT_REGISTRY: tuple[AgentOption, ...] = (
    AgentOption(id="claude-code", label="Claude Code"),
    AgentOption(id="codex", label="Codex"),
)


SUPPORTED_AGENTS = {option.id for option in AGENT_REGISTRY}


def agent_label(agent: AgentName) -> str:
    for option in AGENT_REGISTRY:
        if option.id == agent:
            return option.label
    return agent


def available_agents(app_cfg: AppConfig) -> list[AgentOption]:
    configured = set(app_cfg.agents.keys())
    return [option for option in AGENT_REGISTRY if option.implemented and option.id in configured]
