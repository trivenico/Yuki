"""Dual-model generate/evaluate orchestration with injected providers only."""

import json
from skills.base import Skill, SkillResult


class MultiModelCritiqueSkill(Skill):
    name = "multi_model_critique"
    description = "Generate with one configured model and independently critique with a second; no providers are bundled."
    risk = "medium"

    def __init__(self, generator=None, evaluator=None):
        self.generator = generator
        self.evaluator = evaluator

    def run(self, argument=""):
        try:
            obj = json.loads(argument)
        except (ValueError, TypeError):
            return SkillResult(
                False,
                'Provide JSON {"task":"...","context":"optional"}; input is not sent anywhere by this skill.',
            )
        if (
            not isinstance(obj, dict)
            or not isinstance(obj.get("task"), str)
            or not obj["task"].strip()
            or len(obj["task"]) > 4000
        ):
            return SkillResult(False, "A task of 1–4000 characters is required.")
        if not callable(self.generator) or not callable(self.evaluator):
            return SkillResult(
                False,
                "Two explicitly configured model callbacks are required; no model calls were made.",
            )
        try:
            draft = self.generator(obj["task"], str(obj.get("context", ""))[:4000])
            if not isinstance(draft, str) or not draft.strip() or len(draft) > 20000:
                return SkillResult(False, "Generator returned an invalid or oversized draft.")
            critique = self.evaluator(obj["task"], draft)
            if not isinstance(critique, str) or len(critique) > 12000:
                return SkillResult(False, "Evaluator returned an invalid or oversized critique.")
            return SkillResult(
                True,
                "Draft and independent critique completed; review before relying on either model.",
                {"draft": draft, "critique": critique},
            )
        except Exception as exc:
            return SkillResult(False, f"Model workflow failed safely ({type(exc).__name__}).")
