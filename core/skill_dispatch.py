"""Capability-gated dispatcher for explicitly registered subprocess skills."""
from __future__ import annotations

from typing import Any

from core.action_approvals import ActionApprovalStore
from core.skill_manifest import SkillManifest
from core.subprocess_executor import SubprocessExecutor


class SkillDispatcher:
    def __init__(self, *, capability_grants=(), approval_store: ActionApprovalStore | None = None):
        if isinstance(capability_grants, (str, bytes)):
            raise TypeError("capability_grants must be an iterable of exact capability names.")
        grants = tuple(capability_grants)
        if any(not isinstance(cap, str) or not cap for cap in grants):
            raise ValueError("Capability grants must be non-empty strings.")
        self.capability_grants = frozenset(grants)
        self.approval_store = approval_store or ActionApprovalStore()
        self._registered: dict[str, tuple[SkillManifest, tuple[str, ...], SubprocessExecutor]] = {}

    def register_subprocess(self, manifest: SkillManifest, command, *, executor: SubprocessExecutor):
        if not isinstance(manifest, SkillManifest) or not isinstance(executor, SubprocessExecutor):
            raise TypeError("A validated manifest and SubprocessExecutor are required.")
        if manifest.skill_id in self._registered:
            raise ValueError("Skill is already registered.")
        if not isinstance(command, (list, tuple)) or not command or any(not isinstance(p, str) or not p for p in command):
            raise ValueError("Command must be a non-empty argument list.")
        self._registered[manifest.skill_id] = (manifest, tuple(command), executor)

    def describe(self, skill_id: str) -> dict[str, Any] | None:
        item = self._registered.get(skill_id)
        if not item:
            return None
        manifest = item[0]
        return {"skill_id": manifest.skill_id, "version": manifest.version, "entrypoint": manifest.entrypoint,
                "capabilities": list(manifest.capabilities), "risk": manifest.risk}

    def request_approval(self, skill_id: str, arguments: dict[str, Any]):
        """Create an expiring approval request; a UI must present all returned fields."""
        item = self._registered.get(skill_id)
        if item is None:
            raise PermissionError("Skill is not explicitly registered.")
        manifest = item[0]
        if not set(manifest.capabilities).issubset(self.capability_grants):
            raise PermissionError("Required capability grants are missing.")
        if not SkillManifest.validate_object(manifest.input_schema, arguments):
            raise ValueError("Skill arguments do not match the input schema.")
        approval = self.approval_store.create(skill_id, arguments, risk=manifest.risk)
        return {
            "approval_id": approval.approval_id,
            "skill_id": approval.skill_id,
            "risk": approval.risk,
            "arguments": approval.arguments,
            "payload_sha256": approval.payload_sha256,
            "expires_at": approval.expires_at,
        }

    def execute(self, skill_id: str, arguments: dict[str, Any], *, approval_id: str) -> dict[str, Any]:
        item = self._registered.get(skill_id)
        if item is None:
            raise PermissionError("Skill is not explicitly registered.")
        manifest, command, executor = item
        if not set(manifest.capabilities).issubset(self.capability_grants):
            raise PermissionError("Required capability grants are missing.")
        if not SkillManifest.validate_object(manifest.input_schema, arguments):
            raise ValueError("Skill arguments do not match the input schema.")
        # Atomically consume before execution. Mismatch burns the token, failing closed.
        approved = self.approval_store.consume(
            approval_id, skill_id=skill_id, arguments=arguments, risk=manifest.risk,
        )
        if approved is None:
            raise PermissionError("Missing, expired, replayed, or mismatched approval.")
        # Execute the immutable snapshot that was actually approved, never caller-owned data.
        result = executor.run(
            command, {"skill_id": skill_id, "arguments": approved.arguments},
            timeout_seconds=manifest.timeout_seconds, max_output_bytes=manifest.max_output_bytes,
        )
        if result.get("ok") is not True:
            raise RuntimeError("Skill did not report success.")
        output = result.get("result", {}).get("output") if isinstance(result.get("result"), dict) else None
        if not SkillManifest.validate_object(manifest.output_schema, output):
            raise ValueError("Skill output does not match the output schema.")
        return {"ok": True, "skill_id": skill_id, "output": output}
