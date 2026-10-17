"""Readability editor. Does not promise detector evasion or misrepresent authorship."""
import json
from skills.base import Skill, SkillResult

class AIHumanizerSkill(Skill):
    name="ai_humanizer"
    description="Provide a structured clarity and naturalness edit brief while preserving meaning; no detector-evasion claims."
    risk="low"
    def run(self,argument=""):
        try: obj=json.loads(argument)
        except (ValueError,TypeError): return SkillResult(False,'Provide JSON {"text":"...","tone":"clear","audience":"general"}.')
        if not isinstance(obj,dict) or not isinstance(obj.get("text"),str) or not obj["text"].strip() or len(obj["text"])>6000: return SkillResult(False,"Text must contain 1–6000 characters.")
        tone=obj.get("tone","clear"); audience=obj.get("audience","general")
        if not isinstance(tone,str) or len(tone)>80 or not isinstance(audience,str) or len(audience)>120: return SkillResult(False,"Tone/audience fields are invalid.")
        return SkillResult(True,"Editing brief prepared. No rewriting model is configured; this does not evade AI detectors or certify authorship.",{"source_text":obj["text"].strip(),"edit_instructions":["Preserve factual claims, names, numbers, and uncertainty.","Vary sentence length only where it improves readability.","Prefer concrete verbs and remove redundant phrasing.","Avoid invented anecdotes, citations, or personal experiences."],"tone":tone,"audience":audience})
