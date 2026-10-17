"""Interview practice skill using bounded, deterministic prompts."""
from __future__ import annotations


class InterviewSkill:
    name = "interview"
    description = "Generate a practice interview prompt for a role or competency."
    risk = "low"
    requires_approval = False

    PROMPTS = {
        "behavioral": "Tell me about a difficult problem you solved. Structure your answer using Situation, Task, Action, Result, and what you learned.",
        "technical": "Explain a technical decision you made, the alternatives you considered, trade-offs, and how you verified the result.",
        "security": "Describe how you would investigate a suspicious authentication pattern while preserving evidence and avoiding premature attribution.",
    }

    def execute(self, argument: str = ""):
        topic = (argument or "behavioral").strip().lower()
        if topic not in self.PROMPTS:
            return {"ok": False, "message": "Choose behavioral, technical, or security."}
        return {"ok": True, "topic": topic, "prompt": self.PROMPTS[topic],
                "guidance": "Answer in your own words; Yuki has not evaluated your answer yet."}
