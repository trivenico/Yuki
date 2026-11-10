"""Curated least-privilege specialist profiles for Yuki's durable coordinator.

These are routing profiles, not autonomous executors. Each profile maps to the
existing allow-listed emit worker action; execution still requires a trusted
registered handler, task authorization, and workflow approval where configured.
"""
from __future__ import annotations

from automation.agent_coordinator import SpecialistAgent

# Capabilities are narrow and composable. Permission labels are consumed by
# the coordinator's authorization boundary, not grants to arbitrary tools.
_PROFILES = (
    ("research", "Research specialist", ("research", "source_synthesis"), ("web:read",)),
    ("engineering", "Software engineering specialist", ("code_analysis", "implementation_planning"), ("repository:read",)),
    ("debugging", "Debugging specialist", ("root_cause_analysis", "log_analysis"), ("logs:read",)),
    ("security", "Application security specialist", ("threat_modeling", "security_review"), ("repository:read",)),
    ("testing", "Quality assurance specialist", ("test_design", "regression_analysis"), ("repository:read",)),
    ("documentation", "Technical documentation specialist", ("technical_writing", "documentation_review"), ("artifact:write",)),
    ("architecture", "Systems architecture specialist", ("system_design", "architecture_review"), ("repository:read",)),
    ("devops", "DevOps specialist", ("deployment_planning", "operations_analysis"), ("infrastructure:read",)),
    ("data", "Data engineering specialist", ("data_modeling", "pipeline_analysis"), ("database:read",)),
    ("product", "Product planning specialist", ("requirements_analysis", "roadmap_planning"), ()),
    ("ux", "UX research specialist", ("usability_analysis", "interaction_design"), ()),
    ("performance", "Performance engineering specialist", ("profiling_analysis", "optimization_planning"), ("metrics:read",)),
)


def default_specialist_agents() -> tuple[SpecialistAgent, ...]:
    """Return fresh immutable specialist profiles with conservative permissions.

    No profile receives write, deploy, shell, secret, or merge permissions.
    Extend by explicitly registering a reviewed SpecialistAgent with the
    coordinator; never infer permissions from an agent's display name.
    """
    return tuple(
        SpecialistAgent(
            agent_id=agent_id,
            name=name,
            capabilities=frozenset(capabilities),
            action="emit",
            permissions=frozenset(permissions),
            enabled=True,
        )
        for agent_id, name, capabilities, permissions in _PROFILES
    )


def specialist_profile_ids() -> tuple[str, ...]:
    """Stable profile IDs for dashboards and configuration validation."""
    return tuple(profile[0] for profile in _PROFILES)
