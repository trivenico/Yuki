"""Agentic engineering planner; creates a reviewable plan, never executes code or shell commands."""
import json
from skills.base import Skill, SkillResult

class VibeCoderSkill(Skill):
    name="vibe_coder"
    description="Turn a software brief into a bounded implementation checklist for human review; no code execution."
    risk="medium"
    def run(self,argument=""):
        try: obj=json.loads(argument)
        except (ValueError,TypeError): return SkillResult(False,'Provide JSON {"brief":"...","stack":"Python","constraints":["..."]}.')
        if not isinstance(obj,dict) or not isinstance(obj.get("brief"),str) or not obj["brief"].strip() or len(obj["brief"])>4000: return SkillResult(False,"Brief must contain 1–4000 characters.")
        stack=obj.get("stack","unspecified"); constraints=obj.get("constraints",[])
        if not isinstance(stack,str) or len(stack)>200 or not isinstance(constraints,list) or len(constraints)>20 or any(not isinstance(c,str) or len(c)>300 for c in constraints): return SkillResult(False,"Stack/constraints exceed supported bounds.")
        return SkillResult(True,"Engineering plan prepared; no files were created, commands run, or repository changed.",{"brief":obj["brief"].strip(),"stack":stack,"constraints":constraints,"plan":[{"step":1,"name":"Clarify acceptance criteria","checks":["Define observable behavior","List non-goals and risk boundaries"]},{"step":2,"name":"Inspect current architecture","checks":["Identify existing APIs and conventions","Map dependencies and compatibility constraints"]},{"step":3,"name":"Implement smallest vertical slice","checks":["Keep changes scoped","Validate all external input","Require approval for writes and external actions"]},{"step":4,"name":"Add regression tests","checks":["Cover success, invalid input, and failure paths","Keep tests deterministic"]},{"step":5,"name":"Verify and review","checks":["Run formatter/type checks/tests","Inspect diff and dependency changes","Document limitations and rollback"]}]})
