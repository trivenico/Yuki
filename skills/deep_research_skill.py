"""Deep Research Skill for Yuki."""

import json
from typing import Dict, Any
from skills.base import Skill, SkillResult
from knowledge.deep_research import DeepResearchEngine


class DeepResearchSkill(Skill):
    """Skill to trigger automated deep research and report synthesis."""

    name = "deep_research"
    description = "Executes multi-step deep research and source-attributed report synthesis."
    risk = "low"
    requires_approval = False

    def __init__(self):
        self.engine = DeepResearchEngine()

    def run(self, argument: str = "") -> SkillResult:
        topic = argument
        depth = 2
        if "::" in argument:
            parts = argument.split("::", 1)
            topic = parts[0].strip()
            try:
                depth = int(parts[1].strip())
            except ValueError:
                depth = 2

        res = self.engine.research(topic, depth=depth)
        if "error" in res:
            return SkillResult(False, res["error"])
        return SkillResult(True, res["report"], data=res)
