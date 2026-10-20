"""Local AI mentor planner and progress tracker; no diagnosis or model call by default."""

from pathlib import Path
import uuid
from skills.base import Skill, SkillResult
from skills.safe_local import load_json, save_json, parse_object


class AIMentorSkill(Skill):
    name = "ai_mentor"
    description = "Create learning goals, record progress, and review local learning plans."
    risk = "low"

    def __init__(self, workspace):
        self.path = Path(workspace).resolve() / "mentor_progress.json"

    def run(self, argument=""):
        state = load_json(self.path, {"goals": []})
        if argument.strip() == "list":
            return SkillResult(True, "Local mentor goals.", state["goals"][-100:])
        if argument.strip() == "clear":
            return SkillResult(save_json(self.path, {"goals": []}), "Mentor goals cleared locally.")
        obj = parse_object(argument)
        if not obj or obj.get("action") not in {"goal", "progress"}:
            return SkillResult(
                False,
                'Use list, clear, or JSON {"action":"goal","topic":"Python","target":"Build a small CLI"} / {"action":"progress","id":"...","note":"..."}.',
            )
        if obj["action"] == "goal":
            topic = obj.get("topic")
            target = obj.get("target")
            if (
                not isinstance(topic, str)
                or not topic.strip()
                or len(topic) > 120
                or not isinstance(target, str)
                or not target.strip()
                or len(target) > 500
            ):
                return SkillResult(False, "Topic and target are required and bounded.")
            row = {
                "id": uuid.uuid4().hex[:12],
                "topic": topic.strip(),
                "target": target.strip(),
                "status": "active",
                "progress": [],
            }
            state["goals"].append(row)
            state["goals"] = state["goals"][-200:]
            if not save_json(self.path, state):
                return SkillResult(False, "Could not save mentor plan.")
            return SkillResult(
                True, "Created a local learning goal; no external model was used.", row
            )
        gid = obj.get("id")
        note = obj.get("note")
        if (
            not isinstance(gid, str)
            or not isinstance(note, str)
            or not note.strip()
            or len(note) > 1000
        ):
            return SkillResult(
                False, "A valid goal id and 1–1000 character progress note are required."
            )
        for row in state["goals"]:
            if row["id"] == gid:
                row["progress"].append(note.strip())
                row["progress"] = row["progress"][-100:]
                if not save_json(self.path, state):
                    return SkillResult(False, "Could not save progress.")
                return SkillResult(
                    True,
                    "Progress recorded locally.",
                    {"id": gid, "progress_entries": len(row["progress"])},
                )
        return SkillResult(False, "Goal id not found.")
