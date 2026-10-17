"""Local video workflow manifest; generation requires a separately configured backend."""
from pathlib import Path
from skills.base import Skill, SkillResult
from skills.safe_local import save_json
import json, uuid

class VideoGeneratorSkill(Skill):
    name = "video_generator"
    description = "Create a local, reviewable video-generation job manifest; does not claim to render video."
    risk = "medium"
    def __init__(self, workspace): self.root = Path(workspace).resolve() / "video_jobs"
    def run(self, argument=""):
        try: spec = json.loads(argument)
        except ValueError: return SkillResult(False, 'Provide JSON with a non-empty "prompt" field.')
        if not isinstance(spec, dict) or not isinstance(spec.get("prompt"), str) or not spec["prompt"].strip():
            return SkillResult(False, 'Provide JSON with a non-empty "prompt" field.')
        prompt = spec["prompt"].strip()
        if len(prompt) > 2000: return SkillResult(False, "Prompt exceeds 2000 characters.")
        job = {"id": uuid.uuid4().hex, "prompt": prompt, "status": "queued_for_review",
               "backend": "not_configured", "created_by": "user", "rendered": False}
        path = self.root / (job["id"] + ".json")
        if not save_json(path, job): return SkillResult(False, "Could not save the local job manifest.")
        return SkillResult(True, "Saved a reviewable video job. No video was rendered; configure a local generation backend first.", job)
