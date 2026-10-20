"""Evidence-conscious monetization planning; no income guarantees or transactions."""

from skills.base import Skill, SkillResult


class MonetizationAdvisorSkill(Skill):
    name = "monetization"
    description = "Compare monetization ideas with assumptions, costs, risks and validation steps."
    risk = "low"
    requires_approval = False

    def run(self, argument=""):
        goal = argument.strip()
        if not goal or len(goal) > 500:
            return SkillResult(False, "Describe your skills, audience, budget and time available.")
        return SkillResult(
            True,
            "Planning scaffold only; income is not guaranteed and no accounts or campaigns were launched.",
            {
                "goal": goal,
                "evaluation": [
                    "customer demand evidence",
                    "startup and recurring costs",
                    "time to first test",
                    "distribution channel",
                    "legal/platform constraints",
                    "measurable validation experiment",
                    "stop-loss threshold",
                ],
            },
        )
