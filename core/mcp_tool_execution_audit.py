"""MCP lifecycle adapter for the existing hash-chained approval audit log.

Only fixed-schema metadata is recorded. Tool arguments, credentials, tool results,
and remote response bodies are deliberately excluded.
"""
from __future__ import annotations

import hashlib
import json
import re

from core.approval_audit import SQLiteApprovalAuditLog

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")

_EVENT_MAP = {
    "requested": "requested",
    "owner_approved": "approved",
    "authorized_read_only": "approved",
    "denied": "denied",
    "execution_started": "execution_started",
    "executed": "executed",
    "execution_failed": "execution_failed",
    "outcome_unknown": "outcome_unknown",
}


class MCPToolExecutionAuditor:
    """Callable, identity-bound adapter over SQLiteApprovalAuditLog.

    Bind this instance to the authenticated owner and trusted provider/account.
    Its callback accepts only event metadata emitted by Yuki's policy gate and
    MCP client. Execution IDs are hashed by the underlying audit log before
    persistence; the actor field is a one-way identity fingerprint.
    """

    def __init__(
        self, audit_log: SQLiteApprovalAuditLog, *,
        owner_id: str, provider_id: str, account_id: str,
    ):
        if not isinstance(audit_log, SQLiteApprovalAuditLog):
            raise TypeError("existing_sqlite_approval_audit_log_required")
        for label, value in (
            ("owner_id", owner_id), ("provider_id", provider_id),
            ("account_id", account_id),
        ):
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError("invalid_" + label)
        self._audit = audit_log
        self._owner_id = owner_id
        self._provider_id = provider_id
        self._account_id = account_id
        identity = json.dumps(
            [owner_id, provider_id, account_id],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("ascii")
        self._actor_fingerprint = "mcp:" + hashlib.sha256(identity).hexdigest()

    def __call__(self, event: dict) -> int:
        if not isinstance(event, dict):
            raise ValueError("mcp_audit_event_must_be_object")
        if (event.get("owner_id"), event.get("provider_id"), event.get("account_id")) != (
            self._owner_id, self._provider_id, self._account_id
        ):
            raise ValueError("mcp_audit_identity_mismatch")
        tool_name = event.get("tool_name")
        execution_id = event.get("execution_id")
        event_name = event.get("event")
        if not isinstance(tool_name, str) or not _TOOL_NAME.fullmatch(tool_name):
            raise ValueError("invalid_mcp_audit_tool_name")
        if (not isinstance(execution_id, str) or not re.fullmatch(r"[0-9a-f]{32}", execution_id)):
            raise ValueError("invalid_mcp_execution_id")
        if not isinstance(event_name, str):
            raise ValueError("unsupported_mcp_audit_event")
        mapped = _EVENT_MAP.get(event_name)
        if mapped is None:
            raise ValueError("unsupported_mcp_audit_event")
        # skill_id is bounded to 120 chars by the shared audit log. Keep short
        # names human-readable and use a stable digest for unusually long names.
        skill_id = (
            "mcp:" + tool_name
            if len(tool_name) <= 116
            else "mcp:sha256:" + hashlib.sha256(tool_name.encode("utf-8")).hexdigest()
        )
        return self._audit.append(
            event_type=mapped,
            pending_id=execution_id,
            actor=self._actor_fingerprint,
            skill_id=skill_id,
        )
