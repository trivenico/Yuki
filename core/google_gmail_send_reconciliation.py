"""Gmail provider adapter for read-only reconciliation of uncertain sends.

A missing search result is deliberately inconclusive: Gmail indexing and provider
visibility can lag, so this adapter never converts absence into permission to resend.
"""
from __future__ import annotations

import re

from core.google_gmail_readonly_client import GmailReadOnlyClient
from core.tool_action_reconciliation import ProviderReconciliationResult
from core.tool_action_journal import ToolAction
from core.pending_tool_action_proposals import PendingToolActionProposal

_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")
_ACTION = re.compile(r"^[a-f0-9]{32}$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")


class GmailSendReconciliationAdapter:
    """Verify one deterministic RFC 822 marker in the proposal's exact Gmail account."""

    def __init__(self, *, client: GmailReadOnlyClient, message_id_domain: str,
                 tool_name: str = "gmail.send_email", account_argument: str = "account_id"):
        if not isinstance(client, GmailReadOnlyClient):
            raise TypeError("a trusted read-only Gmail client is required")
        if not isinstance(message_id_domain, str) or not _DOMAIN.fullmatch(message_id_domain):
            raise ValueError("message_id_domain must be a trusted DNS domain")
        if not isinstance(tool_name, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_.-]{0,127}", tool_name
        ):
            raise ValueError("tool_name is invalid")
        if not isinstance(account_argument, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]{0,63}", account_argument
        ):
            raise ValueError("account_argument is invalid")
        self._client = client
        self._domain = message_id_domain.lower()
        self.tool_name = tool_name
        self._account_argument = account_argument

    def message_id_for_action(self, action: ToolAction) -> str:
        if not isinstance(action, ToolAction) or not _ACTION.fullmatch(action.action_id):
            raise ValueError("invalid trusted Gmail action identity")
        return f"<yuki-{action.action_id}@{self._domain}>"

    def reconcile(self, *, action: ToolAction,
                  proposal: PendingToolActionProposal) -> ProviderReconciliationResult:
        if (not isinstance(action, ToolAction)
                or not isinstance(proposal, PendingToolActionProposal)
                or action.tool_name != self.tool_name
                or proposal.tool_name != self.tool_name
                or action.action_id != proposal.action_id
                or action.idempotency_key != proposal.idempotency_key
                or action.request_digest != proposal.request_digest
                or action.state != "outcome_unknown"):
            raise ValueError("Gmail reconciliation action identity mismatch")
        arguments = proposal.arguments
        if not isinstance(arguments, dict):
            raise ValueError("Gmail proposal arguments are invalid")
        account_id = arguments.get(self._account_argument)
        if not isinstance(account_id, str) or not _ACCOUNT.fullmatch(account_id):
            raise ValueError("Gmail proposal account identity is invalid")
        message_id = self.message_id_for_action(action)
        observation = self._client.find_message_by_rfc822_message_id(
            owner_id=proposal.principal_id, account_id=account_id,
            message_id=message_id,
        )
        if not isinstance(observation, dict) or type(observation.get("found")) is not bool:
            raise ValueError("Gmail lookup returned an invalid observation")
        if not observation["found"]:
            return ProviderReconciliationResult(
                outcome="unknown",
                evidence_reference="gmail:rfc822msgid:" + message_id,
                provider_result={"found": False},
            )
        message = observation.get("message")
        if (not isinstance(message, dict)
                or message.get("message_id") != message_id
                or not isinstance(message.get("provider_message_id"), str)):
            raise ValueError("Gmail lookup did not verify the expected message")
        return ProviderReconciliationResult(
            outcome="succeeded",
            evidence_reference="gmail:message:" + message["provider_message_id"],
            provider_result={
                "provider_message_id": message["provider_message_id"],
                "thread_id": message.get("thread_id"),
                "message_id": message_id,
                "label_ids": message.get("label_ids", []),
            },
        )



def build_gmail_send_reconciliation_registration(
    *, client: GmailReadOnlyClient, message_id_domain: str,
):
    """Return host-only reconciliation adapter keyed to the exact Gmail tool."""
    from core.tool_action_reconciliation import ToolActionReconciliationRegistry

    adapter = GmailSendReconciliationAdapter(
        client=client, message_id_domain=message_id_domain,
    )
    return {
        "tool_name": adapter.tool_name,
        "adapter": adapter,
        "registry": ToolActionReconciliationRegistry({
            adapter.tool_name: adapter,
        }),
    }
