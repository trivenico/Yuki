"""Home-security event note reviewer; no camera/alarm is connected by default."""

from pathlib import Path
from skills.base import Skill, SkillResult
from skills.safe_local import load_json, save_json, parse_object
import uuid


class HomeSecuritySkill(Skill):
    name = "home_security"
    description = "Record and review manually supplied home-security events; no live monitoring or alarm control."
    risk = "high"

    def __init__(self, workspace):
        self.path = Path(workspace).resolve() / "security_events.json"

    def run(self, argument=""):
        rows = load_json(self.path, [])
        if argument.strip() == "list":
            return SkillResult(True, "Manual security event log.", rows[-100:])
        obj = parse_object(argument)
        if not obj or not isinstance(obj.get("event"), str):
            return SkillResult(
                False, 'Use list or JSON {"event":"door sensor reported open","severity":"low"}.'
            )
        event = obj["event"].strip()
        severity = obj.get("severity", "unknown")
        if not event or len(event) > 500 or severity not in {"low", "medium", "high", "unknown"}:
            return SkillResult(False, "Event or severity is invalid.")
        rows.append({"id": uuid.uuid4().hex[:12], "event": event, "severity": severity})
        if not save_json(self.path, rows[-500:]):
            return SkillResult(False, "Could not save event.")
        return SkillResult(
            True, "Saved a manually supplied event. No sensor, camera, or alarm was accessed."
        )
