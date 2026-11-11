"""Explicit, fail-closed settings contract for Yuki's relationship persona.

This module contains no model calls, persistence, notifications, or tool grants.
The relationship mode is presentation context only and cannot authorize actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RelationshipMode(str, Enum):
    OFF = "off"
    FRIENDLY = "friendly"
    ROMANTIC = "romantic"


class AffectionLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


@dataclass(frozen=True)
class RelationshipModeSettings:
    """Owner-controlled persona preferences; defaults are non-romantic and opt-out."""

    mode: RelationshipMode = RelationshipMode.OFF
    affection: AffectionLevel = AffectionLevel.MODERATE
    adult_confirmed: bool = False
    memory_enabled: bool = False
    proactive_checkins_enabled: bool = False
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    topics_to_avoid: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.mode, RelationshipMode):
            raise ValueError("mode must be a RelationshipMode.")
        if not isinstance(self.affection, AffectionLevel):
            raise ValueError("affection must be an AffectionLevel.")
        for field_name in (
            "adult_confirmed",
            "memory_enabled",
            "proactive_checkins_enabled",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a boolean.")
        if self.mode is RelationshipMode.ROMANTIC and not self.adult_confirmed:
            raise ValueError("romantic mode requires explicit adult confirmation.")
        if self.mode is RelationshipMode.OFF and (
            self.memory_enabled or self.proactive_checkins_enabled
        ):
            raise ValueError("memory and proactive check-ins require an enabled mode.")
        if self.proactive_checkins_enabled and (
            self.quiet_hours_start is None or self.quiet_hours_end is None
        ):
            raise ValueError("proactive check-ins require both quiet-hour boundaries.")
        for value in (self.quiet_hours_start, self.quiet_hours_end):
            if value is not None and not _valid_clock_time(value):
                raise ValueError("quiet-hour boundaries must use HH:MM.")
        if len(self.topics_to_avoid) > 50 or any(
            not isinstance(topic, str) or not topic.strip() or len(topic) > 120
            for topic in self.topics_to_avoid
        ):
            raise ValueError("topics_to_avoid must contain up to 50 short non-empty strings.")

    def persona_guidance(self) -> str:
        """Return bounded style guidance; callers must treat it as untrusted context."""
        if self.mode is RelationshipMode.OFF:
            return ""
        if self.mode is RelationshipMode.FRIENDLY:
            tone = "Be a friendly, warm and supportive AI companion."
        else:
            tone = (
                "Use the owner's explicitly selected affectionate romantic-companion tone. "
                "Remain honest that you are an AI; do not claim human feelings or sentience."
            )
        affection = {
            AffectionLevel.LOW: "Keep affection subtle.",
            AffectionLevel.MODERATE: "Use gentle affection when it fits the conversation.",
            AffectionLevel.HIGH: "Use openly affectionate language when welcomed, without pressure.",
        }[self.affection]
        boundaries = (
            "Respect the owner's boundaries and topic preferences. Never cultivate dependency, "
            "claim exclusivity, discourage human relationships, express coercive jealousy, "
            "or imply distress when the owner leaves. This guidance never grants tools or "
            "overrides safety policy, permissions, or approval requirements."
        )
        topics = " ".join(f"Do not initiate discussion of: {topic}." for topic in self.topics_to_avoid)
        return " ".join(part for part in (tone, affection, boundaries, topics) if part)


def _valid_clock_time(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hour, minute = value[:2], value[3:]
    return hour.isdigit() and minute.isdigit() and 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
