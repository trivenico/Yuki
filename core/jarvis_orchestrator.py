"""Jarvis-style orchestration primitives for Yuki.

This layer coordinates existing local companion stores and produces structured
proposals. It intentionally does not execute skills, contact services, spend
money, access devices, or run arbitrary commands. Execution stays behind Yuki's
existing PermissionGate and registered SkillRegistry.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from core.companion_memory import CompanionMemory
from core.earning_companion import EarningCompanion
from core.opportunity_research import OpportunityResearch


class ActionRisk(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    EXTERNAL = "external"
    FINANCIAL = "financial"
    DEVICE_CONTROL = "device_control"


@dataclass(frozen=True)
class Capability:
    name: str
    category: str
    description: str
    available: bool
    risk: ActionRisk
    approval_required: bool


@dataclass(frozen=True)
class ActionProposal:
    title: str
    rationale: str
    action_type: str
    risk: ActionRisk
    approval_required: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class DailyBriefing:
    generated_at: str
    coach_style: str
    summary: str
    earning: dict[str, Any]
    research_queue: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    plan: list[dict[str, Any]]
    proposals: list[dict[str, Any]]
    notices: list[str]


class JarvisOrchestrator:
    """Build a truthful capability view and a user-reviewable daily briefing."""

    def __init__(
        self,
        *,
        earning: EarningCompanion,
        memory: CompanionMemory,
        research: OpportunityResearch,
        capabilities: Optional[list[Capability]] = None,
    ) -> None:
        self.earning = earning
        self.memory = memory
        self.research = research
        self._capabilities = capabilities or [
            Capability("earning_tracker", "earning", "Track goals, opportunities, and received revenue.", True, ActionRisk.LOCAL_WRITE, True),
            Capability("opportunity_research", "earning", "Store source-backed earning hypotheses and assessments.", True, ActionRisk.LOCAL_WRITE, True),
            Capability("companion_memory", "companion", "Retrieve and manage user-approved continuity notes.", True, ActionRisk.LOCAL_WRITE, True),
            Capability("daily_coach", "planning", "Generate a deterministic, reviewable daily focus plan.", True, ActionRisk.READ_ONLY, False),
            Capability("web_research", "research", "Retrieve current public sources through a configured provider.", False, ActionRisk.READ_ONLY, False),
            Capability("email_outreach", "communication", "Send external messages only after per-action approval.", False, ActionRisk.EXTERNAL, True),
            Capability("payments_and_purchases", "finance", "Make payments or purchases.", False, ActionRisk.FINANCIAL, True),
            Capability("smart_home_and_devices", "devices", "Control connected devices through explicit integrations.", False, ActionRisk.DEVICE_CONTROL, True),
        ]

    def capability_manifest(self) -> list[dict[str, Any]]:
        """Return only explicit capability metadata; unavailable is never implied."""
        return [
            {
                **asdict(item),
                "risk": item.risk.value,
                "approval_required": item.approval_required,
            }
            for item in self._capabilities
        ]

    def propose_action(
        self, *, title: str, rationale: str, action_type: str,
        risk: ActionRisk, payload: Optional[dict[str, Any]] = None,
    ) -> ActionProposal:
        """Create a reviewable proposal, never execute it.

        Anything external, financial, or device-affecting requires approval.
        Callers must not treat this proposal as a permission token.
        """
        for field, value in (("title", title), ("rationale", rationale), ("action_type", action_type)):
            if not isinstance(value, str) or not value.strip() or len(value) > 500:
                raise ValueError(f"{field} must be non-empty text up to 500 characters")
        if not isinstance(risk, ActionRisk):
            raise ValueError("risk must be an ActionRisk value")
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("payload must be a dictionary")
        needs_approval = risk in {
            ActionRisk.LOCAL_WRITE, ActionRisk.EXTERNAL,
            ActionRisk.FINANCIAL, ActionRisk.DEVICE_CONTROL,
        }
        # Copy the mapping so mutation of the caller's top-level dict cannot alter
        # the returned proposal after review.
        safe_payload = dict(payload or {})
        return ActionProposal(
            title=title.strip(),
            rationale=rationale.strip(),
            action_type=action_type.strip(),
            risk=risk,
            approval_required=needs_approval,
            payload=safe_payload,
        )

    def daily_briefing(
        self, *, available_minutes: int = 240, energy: str = "normal",
        currency: str = "USD", memory_category: Optional[str] = None,
    ) -> DailyBriefing:
        """Summarize locally available state and propose a bounded daily plan."""
        earning = self.earning.summary(currency)
        opportunities = self.research.list_opportunities()
        memories = self.memory.list_memories(memory_category)
        plan = self.research.coach_plan(available_minutes, energy=energy)
        notices = [
            "This briefing uses local stored data; it does not imply live web research.",
            "Estimated opportunity values are not realized revenue.",
            "External messages, purchases, payments, and device control are not executed here.",
        ]
        if not opportunities:
            notices.append("Research queue is empty: start with one buyer problem and collect credible sources.")
        proposals = [
            asdict(self.propose_action(
                title="Review the highest-priority research candidate",
                rationale="Choose one opportunity to validate before spending money or time at scale.",
                action_type="review_research",
                risk=ActionRisk.READ_ONLY,
                payload={"opportunity_id": item["id"], "title": item["title"]},
            ))
            for item in opportunities[:3]
        ]
        proposal_rows = []
        for item in proposals:
            item["risk"] = item["risk"].value
            proposal_rows.append(item)
        return DailyBriefing(
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            coach_style="encouraging, direct, evidence-first",
            summary=(
                f"{earning.active_goals} active earning goal(s), "
                f"{earning.open_opportunities} open earning opportunity/opportunities, "
                f"{earning.recorded_revenue:.2f} {earning.currency} recorded revenue."
            ),
            earning=asdict(earning),
            research_queue=opportunities[:10],
            memories=memories[:50],
            plan=plan,
            proposals=proposal_rows,
            notices=notices,
        )

    @staticmethod
    def briefing_to_dict(briefing: DailyBriefing) -> dict[str, Any]:
        """Convert the dataclass to JSON-friendly primitives."""
        result = asdict(briefing)
        return result
