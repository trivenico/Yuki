"""Approval-gated Gmail send tool adapter for Yuki's guarded runtime.

The model can propose a message, but only the guarded runtime can invoke this
handler with a host-created action context and authenticated principal.
"""
from __future__ import annotations

import json

from core.google_gmail_send_client import GmailSendClient
from core.tool_action_context import ToolActionContext
from core.tool_action_journal import ToolAction

GMAIL_SEND_TOOL_NAME = "gmail.send_email"


class GmailSendTool:
    """Context-aware handler; direct invocation without host identity is denied."""

    def __init__(self, client: GmailSendClient):
        if not isinstance(client, GmailSendClient):
            raise TypeError("a trusted GmailSendClient is required")
        self._client = client

    @staticmethod
    def definition() -> dict:
        """Return the model-facing function schema; all arguments require approval."""
        return {
            "type": "function",
            "function": {
                "name": GMAIL_SEND_TOOL_NAME,
                "description": (
                    "Send an email from an explicitly connected Google account. "
                    "This action always requires the user's explicit approval."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {
                            "type": "string",
                            "description": "Exact connected Google account identifier.",
                            "minLength": 1,
                            "maxLength": 256,
                        },
                        "to": {
                            "oneOf": [
                                {"type": "string", "format": "email"},
                                {"type": "array", "items": {"type": "string", "format": "email"},
                                 "minItems": 1, "maxItems": 20},
                            ],
                        },
                        "subject": {"type": "string", "maxLength": 998},
                        "body": {"type": "string", "maxLength": 900000},
                    },
                    "required": ["account_id", "to", "subject", "body"],
                    "additionalProperties": False,
                },
            },
        }

    def __call__(self, argument: str) -> str:
        raise PermissionError("Gmail sending requires a trusted approved action context.")

    def invoke_action(
        self, argument: str, *, action_context: ToolActionContext,
        principal_id: str,
    ) -> str:
        if not isinstance(action_context, ToolActionContext):
            raise PermissionError("trusted Gmail action context is required")
        if not isinstance(principal_id, str) or not principal_id:
            raise PermissionError("authenticated Gmail owner is required")
        if action_context.principal_id != principal_id or action_context.tool_name != GMAIL_SEND_TOOL_NAME:
            raise PermissionError("Gmail action identity mismatch")
        try:
            payload = json.loads(argument)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("invalid Gmail send request") from None
        if not isinstance(payload, dict) or set(payload) != {"account_id", "to", "subject", "body"}:
            raise ValueError("invalid Gmail send request")
        action = ToolAction(
            action_id=action_context.action_id,
            idempotency_key=action_context.idempotency_key,
            tool_name=action_context.tool_name,
            request_digest=action_context.request_digest,
            state="executing",
            result=None,
            error_code=None,
            created_at="",
            updated_at="",
            version=0,
            lease_owner=None,
            lease_expires_at=None,
        )
        result = self._client.send_email(
            owner_id=principal_id,
            account_id=payload["account_id"],
            action=action,
            to=payload["to"],
            subject=payload["subject"],
            body=payload["body"],
        )
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
