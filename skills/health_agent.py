"""Private local wellness notes; not a diagnostic or treatment system."""

from pathlib import Path
from skills.base import Skill, SkillResult
from skills.safe_local import load_json, save_json, parse_object
import uuid


class HealthAgentSkill(Skill):
    name = "health_agent"
    description = "Store or list user-entered wellness notes locally; not medical advice or emergency monitoring."
    risk = "high"

    def __init__(self, workspace):
        self.path = Path(workspace).resolve() / "wellness_notes.json"

    def run(self, argument=""):
        rows = load_json(self.path, [])
        if argument.strip() == "list":
            return SkillResult(
                True,
                "Local wellness notes; discuss medical decisions with a qualified clinician.",
                [{"id": r["id"], "note": r["note"], "date": r["date"]} for r in rows[-50:]],
            )
        if argument.strip() == "clear":
            if not save_json(self.path, []):
                return SkillResult(False, "Could not clear notes.")
            return SkillResult(True, "Local wellness notes cleared.")
        obj = parse_object(argument)
        if not obj or not isinstance(obj.get("note"), str):
            return SkillResult(
                False, 'Use list, clear, or JSON {"note":"wellness note","date":"YYYY-MM-DD"}.'
            )
        note = obj["note"].strip()
        date = obj.get("date", "")
        if not note or len(note) > 1000 or not isinstance(date, str) or len(date) > 10:
            return SkillResult(False, "Note/date is invalid or too long.")
        rows.append({"id": uuid.uuid4().hex[:12], "note": note, "date": date})
        if not save_json(self.path, rows[-300:]):
            return SkillResult(False, "Could not save wellness note.")
        return SkillResult(
            True,
            "Saved locally. This is not a diagnosis, treatment recommendation, or emergency service.",
        )
