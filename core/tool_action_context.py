"""Trusted host-side identity for a single tool action attempt.

Model-generated tool-call IDs are correlation metadata only. A trusted caller
creates this context before dispatch and retains it across retries of that same
logical request. A new intentional action must receive a new context.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import secrets
from typing import Any, Mapping


_PRINCIPAL = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
_TOOL = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


class ToolActionContextError(ValueError):
    """Raised when a trusted action context does not match dispatch input."""


def _canonical_request(request: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(request, Mapping):
        raise ToolActionContextError("action request must be a JSON object.")
    try:
        encoded = json.dumps(
            dict(request), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        raw = encoded.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ToolActionContextError("action request must be finite JSON data.") from exc
    if len(raw) > 32 * 1024:
        raise ToolActionContextError("action request exceeds the size limit.")
    return encoded, hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ToolActionContext:
    """Immutable host-created binding of principal, tool and canonical request."""

    action_id: str
    idempotency_key: str
    principal_id: str
    tool_name: str
    request_digest: str

    @classmethod
    def create(
        cls, principal_id: str, tool_name: str, request: Mapping[str, Any],
    ) -> "ToolActionContext":
        """Create once at trusted ingress; reuse this object for retries only."""
        if not isinstance(principal_id, str) or not _PRINCIPAL.fullmatch(principal_id):
            raise ToolActionContextError("authenticated principal is invalid.")
        if not isinstance(tool_name, str) or not _TOOL.fullmatch(tool_name):
            raise ToolActionContextError("tool name is invalid.")
        _, digest = _canonical_request(request)
        # Independent random IDs prevent callers from deriving identities from
        # model call IDs or accidentally colliding identical intentional actions.
        return cls(
            action_id=secrets.token_hex(16),
            idempotency_key=secrets.token_hex(24),
            principal_id=principal_id,
            tool_name=tool_name,
            request_digest=digest,
        )

    def verify(
        self, principal_id: str, tool_name: str, request: Mapping[str, Any],
    ) -> None:
        """Fail closed if the authenticated identity or dispatch payload changed."""
        if not isinstance(principal_id, str) or not _PRINCIPAL.fullmatch(principal_id):
            raise ToolActionContextError("authenticated principal is invalid.")
        if not isinstance(tool_name, str) or not _TOOL.fullmatch(tool_name):
            raise ToolActionContextError("tool name is invalid.")
        _, digest = _canonical_request(request)
        if (
            principal_id != self.principal_id
            or tool_name != self.tool_name
            or digest != self.request_digest
        ):
            raise ToolActionContextError("trusted action context does not match dispatch input.")
