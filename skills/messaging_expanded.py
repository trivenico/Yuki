"""Local message drafts/outbox; no messages are sent externally."""
from pathlib import Path
from skills.base import Skill, SkillResult
from skills.safe_local import load_json, save_json, parse_object
import uuid

class MessagingExpandedSkill(Skill):
    name = "messaging_expanded"
    description = "Draft and list local messages; delivery requires a separately reviewed provider integration."
    risk = "high"
    def __init__(self, workspace): self.path = Path(workspace).resolve() / "message_drafts.json"
    def run(self, argument=""):
        rows = load_json(self.path, [])
        if argument.strip() == "list": return SkillResult(True, "Local message drafts; none were sent.", [{"id":r["id"],"recipient":r["recipient"],"subject":r["subject"]} for r in rows[-100:]])
        obj = parse_object(argument)
        if not obj or not isinstance(obj.get("recipient"), str) or not isinstance(obj.get("body"), str):
            return SkillResult(False, 'Use list or JSON {"recipient":"contact label","subject":"...","body":"..."} to create a draft.')
        recipient = obj["recipient"].strip(); body = obj["body"].strip(); subject = obj.get("subject", "")
        if not recipient or len(recipient)>160 or not body or len(body)>5000 or not isinstance(subject,str) or len(subject)>200:
            return SkillResult(False, "Message fields are invalid or too long.")
        rows.append({"id":uuid.uuid4().hex[:12],"recipient":recipient,"subject":subject,"body":body,"status":"draft"})
        if not save_json(self.path, rows[-500:]): return SkillResult(False, "Could not save message draft.")
        return SkillResult(True, "Saved a local draft. No message was sent.")
