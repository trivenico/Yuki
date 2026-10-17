"""Consent-gated identity directory skill; does not access camera or microphone."""
from skills.base import Skill, SkillResult
from companion.identity_manager import IdentityManager
class IdentitySkill(Skill):
    name="identity"
    description="List or manage explicitly consented identity labels; no biometric recognition."
    risk="medium"
    requires_approval=True
    def __init__(self, manager=None): self.manager=manager or IdentityManager()
    def run(self, argument=""):
        parts=argument.strip().split(maxsplit=1); cmd=parts[0].lower() if parts else "list"
        if cmd=="list": return SkillResult(True,"Saved consented identity labels.",self.manager.list())
        if cmd=="delete" and len(parts)==2:
            deleted = self.manager.delete(parts[1])
            return SkillResult(deleted, "Identity deleted." if deleted else "Identity not found.")
        if cmd=="add" and len(parts)==2:
            return SkillResult(False,"Adding an identity requires a separate explicit consent flow; no record was created.")
        return SkillResult(False,"Use identity list or identity delete ID.")
