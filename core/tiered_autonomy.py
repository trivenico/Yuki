"""Tier labels for policy/UI; never overrides the global approval gate."""

from dataclasses import dataclass

TIERS = {
    0: "Observe only",
    1: "Prepare proposals",
    2: "Execute after explicit per-action approval",
}


@dataclass(frozen=True)
class AutonomyDecision:
    allowed: bool
    reason: str
    tier: int


class TieredAutonomyPolicy:
    """A conservative policy descriptor. Higher tiers never silently bypass approvals."""

    def __init__(self, tier=1):
        if tier not in TIERS:
            raise ValueError("tier must be 0, 1, or 2")
        self._tier = tier

    @property
    def tier(self):
        return self._tier

    @property
    def label(self):
        return TIERS[self._tier]

    def evaluate(self, *, changes_state=False, external=False, approved=False):
        if self._tier == 0 and changes_state:
            return AutonomyDecision(False, "Observe-only tier blocks state changes.", self._tier)
        if (changes_state or external) and not approved:
            return AutonomyDecision(
                False,
                "Fresh explicit approval is required for every state-changing or external action.",
                self._tier,
            )
        return AutonomyDecision(
            True, "Allowed within tier; the agent's approval gate remains mandatory.", self._tier
        )

    def set_tier(self, tier, *, approved=False):
        if tier not in TIERS:
            return AutonomyDecision(False, "Unknown tier.", self._tier)
        if not approved:
            return AutonomyDecision(
                False, "Changing autonomy tier requires explicit approval.", self._tier
            )
        self._tier = tier
        return AutonomyDecision(
            True, "Tier updated; per-action approvals remain mandatory.", self._tier
        )
