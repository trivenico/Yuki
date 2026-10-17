"""Explicit local health and habit logging; not medical advice."""
from pathlib import Path
from companion.health_tracker import HealthTracker
from skills.base import Skill, SkillResult

class HealthSkill(Skill):
    name="health_log"
    description="Record or list local health and habit notes."
    risk="medium"
    requires_approval=True
    def __init__(self,workspace): self.tracker=HealthTracker(Path(workspace)/"yuki_health.json")
    def run(self,argument=""):
        parts=argument.split(maxsplit=2)
        if not parts: return SkillResult(False,"Use list [TYPE] or add TYPE | DETAILS.")
        if parts[0]=="list":
            kind=parts[1] if len(parts)>1 else None
            return SkillResult(True,"Local health entries.",self.tracker.list(kind=kind,limit=100))
        if parts[0]=="add" and len(parts)>1:
            fields=argument[len("add "):].split("|",1)
            if len(fields)!=2: return SkillResult(False,"Use add TYPE | DETAILS.")
            item=self.tracker.add(fields[0].strip(),fields[1].strip())
            return SkillResult(True,"Saved locally. This log does not provide medical diagnosis.",item)
        return SkillResult(False,"Use list [TYPE] or add TYPE | DETAILS.")
