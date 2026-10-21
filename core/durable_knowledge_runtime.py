"""Durable approval boundary for workspace knowledge indexing."""
from __future__ import annotations

from pathlib import Path
import secrets


class DurableKnowledgeRuntime:
    ALLOWED = {"knowledge:index"}

    def __init__(self, *, indexer, workspace, coordinator, actor: str, confirm, session_id=None):
        if indexer is None or not callable(getattr(indexer, "index_file", None)):
            raise TypeError("A knowledge indexer is required.")
        if coordinator is None or not callable(getattr(coordinator, "record", None)) or not callable(getattr(coordinator, "approve", None)):
            raise TypeError("A durable approval coordinator is required.")
        if not isinstance(actor, str) or not actor.strip() or not callable(confirm):
            raise ValueError("A trusted actor and confirmation callback are required.")
        self.indexer = indexer
        self.workspace = Path(workspace).expanduser().resolve()
        self.coordinator = coordinator
        self.actor = actor
        self.confirm = confirm
        self.session_id = session_id or secrets.token_urlsafe(24)

    def _validate_relative_path(self, path: str) -> str:
        if not isinstance(path, str) or not path.strip() or len(path) > 1024:
            raise ValueError("Provide a valid workspace-relative file path.")
        candidate = Path(path.strip())
        if candidate.is_absolute():
            raise ValueError("Absolute paths are not accepted; use a workspace-relative path.")
        resolved = (self.workspace / candidate).resolve(strict=True)
        if self.workspace not in resolved.parents or resolved.is_symlink() or not resolved.is_file():
            raise ValueError("Only regular files inside the configured workspace can be indexed.")
        return resolved.relative_to(self.workspace).as_posix()

    def index(self, path: str):
        try:
            relative = self._validate_relative_path(path)
        except (OSError, RuntimeError, ValueError) as exc:
            return {"ok": False, "status": "invalid_input", "message": str(exc)}
        action = {"skill_id": "knowledge:index", "arguments": {"relative_path": relative}}
        try:
            pending = self.coordinator.record(None, {"action": action}, actor=self.actor, session_id=self.session_id)
            outcome = self.coordinator.approve(
                self, pending.pending_id, actor=self.actor, session_id=self.session_id,
                confirm=lambda display_action: self.confirm(display_action),
            )
        except Exception:
            return {"ok": False, "status": "recovery_required", "message": "Indexing failed safely; no success was confirmed."}
        if outcome.status != "completed":
            return {"ok": False, "status": outcome.status, "message": f"Knowledge indexing did not complete ({outcome.status})."}
        result = outcome.result
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            return {"ok": False, "status": "invalid_result_contract", "message": "Invalid indexing result contract."}
        return result

    def execute_approved_action(self, action: dict):
        if not isinstance(action, dict) or set(action) != {"skill_id", "arguments"}:
            raise ValueError("Malformed approved knowledge action.")
        if action["skill_id"] not in self.ALLOWED or not isinstance(action["arguments"], dict):
            raise ValueError("Unsupported approved knowledge action.")
        if set(action["arguments"]) != {"relative_path"}:
            raise ValueError("Malformed approved knowledge payload.")
        relative = self._validate_relative_path(action["arguments"]["relative_path"])
        result = self.indexer.index_file(relative, workspace=self.workspace)
        return {"ok": True, "message": "Knowledge file indexed.", "data": result}
