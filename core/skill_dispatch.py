"""Policy-enforcing dispatcher for already-loaded, trusted built-in skills.

This validates contracts and grants before invocation. It does NOT sandbox Python
code; third-party plugins must not be registered here until isolated execution exists.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from core.skill_manifest import SkillManifest


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    code: str
    output: Any = None


class SkillDispatcher:
    def __init__(self, *, capability_grants: set[str] | frozenset[str],
                 approval: Callable[[str, dict[str, Any]], bool] | None = None):
        if not isinstance(capability_grants, (set, frozenset)):
            raise TypeError("capability_grants must be a set of explicitly granted capabilities.")
        self._grants = frozenset(capability_grants)
        self._approval = approval
        self._skills: dict[str, tuple[SkillManifest, Callable[[dict[str, Any]], Any]]] = {}

    def register(self, manifest: SkillManifest, handler: Callable[[dict[str, Any]], Any]) -> None:
        if not isinstance(manifest, SkillManifest) or not callable(handler):
            raise TypeError("A validated SkillManifest and callable handler are required.")
        if manifest.skill_id in self._skills:
            raise ValueError("Duplicate skill identifier.")
        self._skills[manifest.skill_id] = (manifest, handler)

    def dispatch(self, skill_id: str, arguments: dict[str, Any]) -> DispatchResult:
        registered = self._skills.get(skill_id)
        if registered is None:
            return DispatchResult(False, "skill_not_registered")
        manifest, handler = registered
        if not manifest.capabilities.issubset(self._grants):
            return DispatchResult(False, "capability_denied")
        try:
            manifest.validate_input(arguments)
        except ValueError:
            return DispatchResult(False, "invalid_input")
        action = {
            "skill_id": manifest.skill_id,
            "version": manifest.version,
            "entrypoint": manifest.entrypoint,
            "arguments": arguments,
            "capabilities": sorted(manifest.capabilities),
        }
        if manifest.risk in {"medium", "high"}:
            if self._approval is None:
                return DispatchResult(False, "approval_required")
            try:
                if self._approval(manifest.risk, action) is not True:
                    return DispatchResult(False, "approval_denied")
            except Exception:
                return DispatchResult(False, "approval_error")
        try:
            output = handler(arguments)
            manifest.validate_output(output)
            # Force strict JSON encoding to reject non-finite values and objects that
            # may accidentally retain opaque runtime handles.
            json.dumps(output, ensure_ascii=False, allow_nan=False)
        except ValueError:
            return DispatchResult(False, "invalid_output")
        except Exception:
            return DispatchResult(False, "handler_failed")
        return DispatchResult(True, "ok", output)
