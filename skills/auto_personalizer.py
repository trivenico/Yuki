"""Opt-in, local usage-preference store; never profiles without explicit input."""

from pathlib import Path
from skills.base import Skill, SkillResult
from skills.safe_local import load_json, save_json, parse_object


class AutoPersonalizerSkill(Skill):
    name = "auto_personalizer"
    description = (
        "Explicitly record, inspect, or clear local preference signals for personalization."
    )
    risk = "medium"

    def __init__(self, workspace):
        self.path = Path(workspace).resolve() / "personalization.json"

    def run(self, argument=""):
        data = load_json(self.path, {"enabled": False, "signals": []})
        if argument.strip() == "show":
            return SkillResult(
                True,
                "Local personalization status.",
                {
                    "enabled": data.get("enabled", False),
                    "signal_count": len(data.get("signals", [])),
                },
            )
        if argument.strip() == "clear":
            if not save_json(self.path, {"enabled": False, "signals": []}):
                return SkillResult(False, "Could not clear preferences.")
            return SkillResult(True, "Local preference signals cleared.")
        obj = parse_object(argument)
        if not obj or obj.get("action") != "record" or not isinstance(obj.get("signal"), str):
            return SkillResult(
                False, 'Use show, clear, or JSON: {"action":"record","signal":"...","opt_in":true}.'
            )
        if obj.get("opt_in") is not True:
            return SkillResult(False, "Explicit opt_in=true is required.")
        signal = obj["signal"].strip()
        if not signal or len(signal) > 240:
            return SkillResult(False, "Signal must be 1–240 characters.")
        data = {"enabled": True, "signals": (data.get("signals", []) + [signal])[-100:]}
        if not save_json(self.path, data):
            return SkillResult(False, "Could not save preference.")
        return SkillResult(
            True,
            "Saved one opt-in local preference signal.",
            {"signal_count": len(data["signals"])},
        )
