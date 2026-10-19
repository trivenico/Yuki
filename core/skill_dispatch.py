"""Dispatch validated manifest skills to pre-registered subprocess argv."""
from dataclasses import dataclass
from core.skill_manifest import validate_object

@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    status: str
    skill_id: str | None = None
    result: dict | None = None
    approval_id: str | None = None
    message: str = ""

class SkillDispatcher:
    def __init__(self, *, capability_grants=(), approval_store=None):
        self.capability_grants = frozenset(capability_grants)
        self.approval_store = approval_store
        self._handlers = {}

    def register_subprocess(self, manifest, argv, *, executor):
        if not hasattr(manifest, "skill_id"): raise TypeError("validated manifest required")
        if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(x, str) or not x for x in argv): raise ValueError("argv required")
        missing = set(manifest.capabilities) - self.capability_grants
        if missing: raise PermissionError("capability grant missing")
        if manifest.skill_id in self._handlers: raise ValueError("duplicate skill id")
        self._handlers[manifest.skill_id] = (manifest, tuple(argv), executor)

    def describe(self, skill_id, arguments):
        entry = self._handlers.get(skill_id)
        if not entry: raise KeyError("skill not registered")
        manifest, _, _ = entry
        validate_object(arguments, manifest.input_schema)
        return {"skill_id": skill_id, "arguments": dict(arguments), "capabilities": list(manifest.capabilities), "risk": manifest.risk}

    def dispatch(self, skill_id, arguments, *, approved=False):
        entry = self._handlers.get(skill_id)
        if not entry: return DispatchResult(False, "not_registered", skill_id, message="Skill is not registered")
        manifest, argv, executor = entry
        try: validate_object(arguments, manifest.input_schema)
        except ValueError as exc: return DispatchResult(False, "invalid_arguments", skill_id, message=str(exc))
        if not set(manifest.capabilities).issubset(self.capability_grants): return DispatchResult(False, "capability_denied", skill_id)
        if manifest.risk in {"high", "critical"} and not approved:
            return DispatchResult(False, "approval_required", skill_id, message="Explicit approval required")
        try:
            raw = executor.execute(argv, dict(arguments), timeout_seconds=manifest.timeout_seconds, max_output_bytes=manifest.max_output_bytes)
            result = raw.get("result", raw)
            validate_object(result, manifest.output_schema)
            return DispatchResult(bool(raw.get("ok", True)), "completed" if raw.get("ok", True) else "failed", skill_id, result)
        except Exception:
            return DispatchResult(False, "execution_failed", skill_id, message="Skill execution failed safely")
