"""Smart-home adapter boundary. No devices are contacted unless an adapter is supplied."""
from skills.base import Skill, SkillResult

class SmartHomeFullSkill(Skill):
    name = "smart_home_full"
    description = "Inspect smart-home integration readiness; device control requires a reviewed vendor adapter."
    risk = "high"
    def __init__(self, adapter=None): self.adapter = adapter
    def run(self, argument=""):
        if self.adapter is None:
            return SkillResult(False, "Smart-home adapter is not configured. No devices were contacted.", {"supported": False, "commands": ["status"]})
        if argument.strip() != "status":
            return SkillResult(False, "Only status inspection is supported by this wrapper; device writes need a separately reviewed adapter and approval.")
        try: return SkillResult(True, "Smart-home adapter status.", self.adapter.status())
        except Exception as exc: return SkillResult(False, f"Adapter failed safely ({type(exc).__name__}).")
