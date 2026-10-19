"""Dispatch skill executions based on manifests and capability grants."""
import json
import subprocess


class SkillDispatcher:
    def __init__(self, capability_grants=None, approval_store=None):
        self.capability_grants = set(capability_grants or [])
        self.approval_store = approval_store
        self.registered = {}

    def register_subprocess(self, manifest, cmd, executor=None):
        self.registered[manifest.skill_id] = {
            "manifest": manifest,
            "cmd": cmd,
            "executor": executor,
        }

    def dispatch(self, skill_id, arguments):
        if skill_id not in self.registered:
            raise ValueError(f"Unknown skill: {skill_id}")
        entry = self.registered[skill_id]
        manifest = entry["manifest"]
        for cap in manifest.capabilities:
            if cap not in self.capability_grants:
                raise PermissionError(f"Missing capability: {cap}")

        cmd = entry["cmd"]
        executor = entry["executor"]
        input_json = json.dumps(arguments)
        if executor:
            stdout, stderr, code = executor.run(cmd, input_data=input_json)
        else:
            proc = subprocess.run(cmd, input=input_json, text=True, capture_output=True, timeout=manifest.timeout_seconds)
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode

        res = json.loads(stdout)
        return res
