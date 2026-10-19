"""Defensive device posture report from user-provided inventory; no network probing."""
from skills.base import Skill, SkillResult
from skills.safe_local import parse_object

class DeviceScannerSkill(Skill):
    name = "device_scanner"
    description = "Review a user-provided device inventory for basic defensive hardening gaps; performs no scanning."
    risk = "medium"

    def __init__(self, workspace=None):
        self.workspace = workspace

    def run(self, argument=""):
        obj = parse_object(argument)
        if not obj or not isinstance(obj.get("device"), str) or not isinstance(obj.get("updates_current"), bool):
            return SkillResult(False, 'Provide inventory JSON: {"device":"phone model","updates_current":true,"screen_lock":true,"encryption":true,"unknown_apps":false}.')
        checks = {"updates_current":"Install OS/security updates.", "screen_lock":"Enable a strong screen lock.", "encryption":"Confirm device storage encryption.", "unknown_apps":"Review unknown or sideloaded apps."}
        findings = [advice for key, advice in checks.items() if (obj.get(key) is False if key != "unknown_apps" else obj.get(key) is True)]
        return SkillResult(True, "Inventory-only posture review. No phone, network, or device was scanned.", {"device":obj["device"][:100], "findings":findings, "automated_vulnerability_scan":False})
