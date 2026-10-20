"""Project-building intake and plan generator; no files, Git or deployments are changed."""

from skills.base import Skill, SkillResult


class ProjectSkill(Skill):
    name = "project"
    description = "Turn a project goal into a bounded build/test/review plan."
    risk = "medium"
    requires_approval = False

    def run(self, argument=""):
        goal = argument.strip()
        if not goal or len(goal) > 500:
            return SkillResult(False, "Describe the project in 1-500 characters.")
        steps = [
            {"step": "inspect workspace and requirements", "approval": False},
            {"step": "propose implementation and tests", "approval": False},
            {"step": "review diff and run tests", "approval": True},
            {"step": "commit and push changes", "approval": True},
            {"step": "publish or deploy", "approval": True},
        ]
        return SkillResult(
            True,
            "Plan created only; no files were changed and nothing was pushed or published.",
            {"goal": goal, "steps": steps},
        )
