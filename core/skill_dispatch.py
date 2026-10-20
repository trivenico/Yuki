"""Capability-gated dispatcher for approved manifest actions.

The approval receipt is an in-process guard against accidental direct execution.
It is not a sandbox boundary against arbitrary code already running in this Python
process; untrusted skill code must remain in the subprocess worker.
"""

import json
import secrets
from dataclasses import dataclass

from core.skill_manifest import SkillManifest
from core.subprocess_executor import SubprocessExecutor


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    status: str
    message: str
    data: dict | None = None


class SkillDispatcher:
    def __init__(self, *, capability_grants=(), approval_store=None):
        self.capability_grants = frozenset(capability_grants)
        self.approval_store = approval_store
        self._workers = {}
        self._receipt_secret = secrets.token_urlsafe(32)
        self._issued_receipts = set()

    def register_subprocess(self, manifest, command, *, executor=None):
        if not isinstance(manifest, SkillManifest):
            raise TypeError("manifest must be validated")
        if (
            not isinstance(command, (list, tuple))
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise ValueError("invalid command")
        if any(capability not in self.capability_grants for capability in manifest.capabilities):
            raise PermissionError("ungranted capability")
        if manifest.skill_id in self._workers:
            raise ValueError("duplicate skill")
        self._workers[manifest.skill_id] = (
            manifest,
            tuple(command),
            executor
            or SubprocessExecutor(
                timeout_seconds=manifest.timeout_seconds, max_output_bytes=manifest.max_output_bytes
            ),
        )

    def prepare(self, skill_id, arguments):
        if skill_id not in self._workers:
            raise ValueError("unknown skill")
        manifest, _, _ = self._workers[skill_id]
        args = manifest.validate_object(arguments, manifest.input_schema)
        args = json.loads(json.dumps(args, allow_nan=False))
        return {
            "skill_id": skill_id,
            "arguments": args,
            "manifest_version": manifest.version,
            "capabilities": list(manifest.capabilities),
            "risk": manifest.risk,
        }

    def _issue_approval_receipt(self):
        """Mint a single-use receipt for the approval coordinator."""
        receipt = secrets.token_urlsafe(32)
        self._issued_receipts.add(receipt)
        return receipt

    def execute_approved(self, action, *, approval_receipt=None):
        """Execute only after the coordinator supplies a valid, single-use receipt."""
        if (
            not isinstance(approval_receipt, str)
            or approval_receipt not in self._issued_receipts
        ):
            return DispatchResult(False, "approval_required", "Human approval is required.")
        self._issued_receipts.remove(approval_receipt)
        try:
            if not isinstance(action, dict):
                raise ValueError("invalid action")
            manifest, command, executor = self._workers[action["skill_id"]]
            if (
                action.get("manifest_version") != manifest.version
                or action.get("capabilities") != list(manifest.capabilities)
                or action.get("risk") != manifest.risk
            ):
                raise ValueError("manifest changed")
            arguments = manifest.validate_object(action["arguments"], manifest.input_schema)
            response = executor.execute(
                command,
                {
                    "skill_id": manifest.skill_id,
                    "arguments": arguments,
                    "entrypoint": manifest.entrypoint,
                },
                timeout_seconds=manifest.timeout_seconds,
                max_output_bytes=manifest.max_output_bytes,
            )
            if response.get("ok") is not True or not isinstance(response.get("result"), dict):
                raise ValueError("invalid worker envelope")
            output = manifest.validate_object(
                response["result"].get("output"), manifest.output_schema
            )
            return DispatchResult(True, "completed", "Skill completed.", {"output": output})
        except Exception:
            return DispatchResult(False, "execution_failed", "Skill execution failed safely.")
