"""Policy-enforcing dispatcher for explicitly trusted handlers.

The dispatcher validates contracts and grants. It does not sandbox Python code or
provide hard execution timeouts; do not register untrusted plugins here.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from core.action_approvals import ActionApprovalStore
from core.skill_manifest import SkillManifest
from core.subprocess_executor import SubprocessExecutor


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    code: str
    output: Any = None


_SIDE_EFFECT_CAPABILITIES = frozenset({
    "workspace.write", "memory.write", "messaging.send", "process.spawn"
})


class SkillDispatcher:
    def __init__(self, *, capability_grants: set[str] | frozenset[str],
                 approval_store: ActionApprovalStore | None = None):
        if not isinstance(capability_grants, (set, frozenset)):
            raise TypeError("capability_grants must be a set of explicitly granted capabilities.")
        self._grants = frozenset(capability_grants)
        self._approval_store = approval_store
        self._skills: dict[str, tuple[SkillManifest, Callable[[dict[str, Any]], Any]]] = {}

    def register(self, manifest: SkillManifest, handler: Callable[[dict[str, Any]], Any]) -> None:
        if not isinstance(manifest, SkillManifest) or not callable(handler):
            raise TypeError("A validated SkillManifest and callable handler are required.")
        if manifest.skill_id in self._skills:
            raise ValueError("Duplicate skill identifier.")
        self._skills[manifest.skill_id] = (manifest, handler)

    def register_subprocess(self, manifest: SkillManifest, argv: tuple[str, ...] | list[str], *,
                            executor: SubprocessExecutor | None = None,
                            cwd: str | None = None,
                            env: dict[str, str] | None = None) -> None:
        """Register a trusted, fixed executable behind the bounded subprocess protocol.

        argv/cwd/env are deployment configuration, not model/plugin-controlled values.
        This provides process separation and bounded IPC, but not an OS sandbox.
        """
        if not isinstance(argv, (tuple, list)) or not argv or any(not isinstance(arg, str) or not arg for arg in argv):
            raise ValueError("argv must be a non-empty fixed sequence of strings.")
        fixed_argv = tuple(argv)
        fixed_env = dict(env or {})
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in fixed_env.items()):
            raise ValueError("env must map strings to strings.")
        bounded_executor = executor or SubprocessExecutor(
            timeout_seconds=min(max(float(manifest.timeout_seconds), 0.1), 300.0),
            max_response_bytes=min(max(int(manifest.max_output_bytes), 1), 1024 * 1024),
        )

        def invoke(arguments: dict[str, Any]) -> Any:
            result = bounded_executor.execute(fixed_argv, {"arguments": arguments}, cwd=cwd, env=fixed_env)
            if not result.ok:
                raise RuntimeError(result.code)
            response = result.result
            if not isinstance(response, dict) or set(response) != {"output"}:
                raise ValueError("Worker response must contain only output.")
            return response["output"]

        self.register(manifest, invoke)

    @staticmethod
    def action_for(manifest: SkillManifest, arguments: dict[str, Any]) -> dict[str, Any]:
        """Canonical approval target; UI must display this exact operation before issuing approval."""
        return {
            "skill_id": manifest.skill_id,
            "version": manifest.version,
            "entrypoint": manifest.entrypoint,
            "arguments": arguments,
            "capabilities": sorted(manifest.capabilities),
        }

    def issue_approval(self, skill_id: str, arguments: dict[str, Any], *,
                       actor: str, session_id: str, approved: bool):
        """Issue approval only after the caller has displayed the exact action and received consent."""
        registered = self._skills.get(skill_id)
        if registered is None:
            raise ValueError("Skill is not registered.")
        manifest, _ = registered
        if not manifest.capabilities.issubset(self._grants):
            raise PermissionError("Skill capability grant is missing.")
        manifest.validate_input(arguments)
        snapshot = json.loads(json.dumps(arguments, ensure_ascii=False, allow_nan=False))
        action = self.action_for(manifest, snapshot)
        if not (manifest.capabilities & _SIDE_EFFECT_CAPABILITIES or manifest.risk in {"medium", "high"}):
            raise ValueError("This skill does not require an approval token.")
        if self._approval_store is None:
            raise RuntimeError("No approval store is configured.")
        return self._approval_store.issue(action, actor=actor, session_id=session_id, approved=approved)

    def dispatch(self, skill_id: str, arguments: dict[str, Any], *,
                 approval_token: str | None = None, actor: str | None = None,
                 session_id: str | None = None) -> DispatchResult:
        registered = self._skills.get(skill_id)
        if registered is None:
            return DispatchResult(False, "skill_not_registered")
        manifest, handler = registered
        if not manifest.capabilities.issubset(self._grants):
            return DispatchResult(False, "capability_denied")
        try:
            manifest.validate_input(arguments)
            # Snapshot the validated JSON so caller mutation cannot alter the action
            # after approval checks or while a handler is running.
            arguments_snapshot = json.loads(json.dumps(arguments, ensure_ascii=False, allow_nan=False))
        except (ValueError, TypeError):
            return DispatchResult(False, "invalid_input")
        action = self.action_for(manifest, arguments_snapshot)
        requires_approval = bool(manifest.capabilities & _SIDE_EFFECT_CAPABILITIES) or manifest.risk in {"medium", "high"}
        if requires_approval:
            if self._approval_store is None or not approval_token or not actor or not session_id:
                return DispatchResult(False, "approval_token_required")
            if not self._approval_store.consume(approval_token, action, actor=actor, session_id=session_id):
                return DispatchResult(False, "approval_invalid_or_expired")
        try:
            output = handler(arguments_snapshot)
            manifest.validate_output(output)
            output_snapshot = json.loads(json.dumps(output, ensure_ascii=False, allow_nan=False))
            manifest.validate_output(output_snapshot)
        except ValueError:
            return DispatchResult(False, "invalid_output")
        except Exception:
            return DispatchResult(False, "handler_failed")
        return DispatchResult(True, "ok", output_snapshot)
