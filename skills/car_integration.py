"""Car integration readiness wrapper; no vehicle bus or phone projection access."""

from skills.base import Skill, SkillResult


class CarIntegrationSkill(Skill):
    name = "car_integration"
    description = "Report CarPlay/Android Auto bridge readiness; does not connect to a vehicle."
    risk = "high"

    def __init__(self, adapter=None):
        self.adapter = adapter

    def run(self, argument=""):
        if self.adapter is None:
            return SkillResult(
                False,
                "No CarPlay/Android Auto adapter is configured. No vehicle was contacted.",
                {"supported": False, "write_actions_enabled": False},
            )
        if argument.strip() != "status":
            return SkillResult(False, "Only adapter status is allowed in this wrapper.")
        try:
            return SkillResult(True, "Vehicle bridge status.", self.adapter.status())
        except Exception as exc:
            return SkillResult(False, f"Adapter failed safely ({type(exc).__name__}).")
