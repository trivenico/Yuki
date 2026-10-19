"""Stitch AI UI design generation skill for Yuki."""
from skills.base import Skill, SkillResult
from skills.safe_local import parse_object


class StitchSkill(Skill):
    name = "stitch_ui"
    description = "Create UI design projects and generate UI screens using Stitch AI."
    risk = "medium"

    def run(self, argument=""):
        obj = parse_object(argument)
        if not obj or not isinstance(obj.get("action"), str):
            return SkillResult(False, 'Provide argument JSON: {"action":"create|generate","title":"Project Title","prompt":"UI prompt","project_id":"..."}')

        action = obj["action"].strip().lower()
        if action == "create":
            title = obj.get("title", "New Yuki Project")
            return SkillResult(
                True,
                f"Stitch project '{title}' request prepared.",
                {"action": "create_project", "title": title, "status": "prepared"}
            )
        elif action == "generate":
            prompt = obj.get("prompt", "")
            project_id = obj.get("project_id", "")
            if not prompt or not project_id:
                return SkillResult(False, "Generation requires both project_id and prompt.")
            return SkillResult(
                True,
                f"Stitch screen generation request prepared for project {project_id}.",
                {"action": "generate_screen", "project_id": project_id, "prompt": prompt, "status": "prepared"}
            )
        else:
            return SkillResult(False, "Supported Stitch actions: 'create', 'generate'.")
