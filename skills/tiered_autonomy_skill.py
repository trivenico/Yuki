"""Inspect the tiered autonomy policy; changes require explicit confirmation plus agent approval."""
from pathlib import Path
from skills.base import Skill, SkillResult
from core.tiered_autonomy import TieredAutonomyPolicy, TIERS
from skills.safe_local import load_json, save_json

class TieredAutonomySkill(Skill):
    name = "tiered_autonomy"
    description = "Inspect or explicitly change Yuki's conservative autonomy tier; cannot disable per-action approval."
    risk = "high"
    def __init__(self, workspace):
        self.path = Path(workspace).resolve() / "autonomy_policy.json"
        saved = load_json(self.path, {"tier":1})
        self.policy = TieredAutonomyPolicy(saved.get("tier",1) if saved.get("tier") in TIERS else 1)
    def run(self, argument=""):
        if argument.strip() in {"", "show"}:
            return SkillResult(True, "Current autonomy tier; explicit approval for state changes/external actions is always retained.", {"tier":self.policy.tier,"label":self.policy.label,"tiers":TIERS})
        parts = argument.strip().split()
        if len(parts) != 3 or parts[0] != "confirm" or parts[1] != "set" or not parts[2].isdigit():
            return SkillResult(False, "Use show or confirm set 0|1|2. Agent approval is still required.")
        tier = int(parts[2]); decision = self.policy.set_tier(tier, approved=True)
        if not decision.allowed: return SkillResult(False, decision.reason)
        if not save_json(self.path, {"tier":tier}): return SkillResult(False, "Could not persist tier.")
        return SkillResult(True, decision.reason, {"tier":tier,"label":self.policy.label})
