"""Single-attempt Gmail send client with action-bound RFC 822 correlation.

This client never retries. Any transport or provider response ambiguity is surfaced
as an unknown outcome for the durable action journal to reconcile separately.
"""
from __future__ import annotations

import base64
import json
import re
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from urllib.request import Request

from core.oauth_provider_credentials import (
    OAuthProviderCredentialError, OAuthProviderCredentialResolver,
)
from core.tool_action_journal import ToolAction

_GMAIL_API = "https://gmail.googleapis.com"
_ACTION = re.compile(r"^[a-f0-9]{32}$")
_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")
_GMAIL_ID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_TOOL_NAME = "gmail.send_email"


class GmailSendError(RuntimeError):
    """Stable send failure; callers must treat dispatch outcome as unknown."""


class GmailSendClient:
    """Send one approved action through Gmail using the exact connected account."""

    MAX_REQUEST_BYTES = 1_000_000
    MAX_RESPONSE_BYTES = 64_000

    def __init__(self, *, credential_resolver: OAuthProviderCredentialResolver,
                 transport, message_id_domain: str, provider_id: str = "google",
                 refresh_service=None):
        if not isinstance(credential_resolver, OAuthProviderCredentialResolver):
            raise TypeError("an owner-scoped OAuth credential resolver is required")
        if not callable(getattr(transport, "open", None)):
            raise TypeError("a pinned HTTPS transport is required")
        if provider_id != "google":
            raise ValueError("Gmail client requires the trusted Google provider")
        if "gmail.googleapis.com" not in getattr(transport, "allowed_hosts", ()):
            raise ValueError("pinned transport must explicitly allow Gmail API host")
        if (not isinstance(message_id_domain, str)
                or not re.fullmatch(
                    r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}",
                    message_id_domain)):
            raise ValueError("message_id_domain must be a trusted DNS domain")
        if refresh_service is not None and (
            not callable(getattr(refresh_service, "refresh_if_needed", None))
            or getattr(refresh_service, "provider_id", None) != provider_id
        ):
            raise TypeError("a matching trusted OAuth refresh service is required")
        self._credentials = credential_resolver
        self._refresh = refresh_service
        self._transport = transport
        self._provider_id = provider_id
        self._domain = message_id_domain.lower()

    @staticmethod
    def _recipients(value):
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple)):
            values = list(value)
        else:
            raise GmailSendError("invalid_gmail_recipients")
        if not 1 <= len(values) <= 20:
            raise GmailSendError("invalid_gmail_recipients")
        normalized = []
        for item in values:
            if (not isinstance(item, str) or not item or len(item) > 320
                    or any(ord(char) < 33 or ord(char) > 126 for char in item)
                    or any(char in item for char in ",;<>")):
                raise GmailSendError("invalid_gmail_recipient")
            try:
                address = Address(addr_spec=item)
            except (TypeError, ValueError):
                raise GmailSendError("invalid_gmail_recipient") from None
            if not address.username or not address.domain or address.addr_spec != item:
                raise GmailSendError("invalid_gmail_recipient")
            normalized.append(item)
        if len(set(normalized)) != len(normalized):
            raise GmailSendError("duplicate_gmail_recipient")
        return normalized

    @staticmethod
    def _text(value, label, maximum, *, allow_empty=False):
        if not isinstance(value, str) or len(value) > maximum:
            raise GmailSendError("invalid_gmail_" + label)
        if not allow_empty and not value.strip():
            raise GmailSendError("invalid_gmail_" + label)
        if (any(ord(char) == 0 or ord(char) == 127 for char in value)
                or (label == "subject" and any(ord(char) < 32 for char in value))):
            raise GmailSendError("invalid_gmail_" + label)
        return value

    def message_id_for_action(self, action: ToolAction) -> str:
        if (not isinstance(action, ToolAction) or action.tool_name != _TOOL_NAME
                or not isinstance(action.action_id, str)
                or not _ACTION.fullmatch(action.action_id)):
            raise GmailSendError("invalid_gmail_action_identity")
        return f"<yuki-{action.action_id}@{self._domain}>"

    def send_email(self, *, owner_id: str, account_id: str, action: ToolAction,
                   to, subject: str, body: str) -> dict:
        """Make exactly one POST; any ambiguous response must be reconciled, never retried."""
        if not isinstance(owner_id, str) or not owner_id:
            raise GmailSendError("invalid_gmail_owner")
        if not isinstance(account_id, str) or not _ACCOUNT.fullmatch(account_id):
            raise GmailSendError("invalid_gmail_account")
        message_id = self.message_id_for_action(action)
        recipients = self._recipients(to)
        subject = self._text(subject, "subject", 998)
        body = self._text(body, "body", 900_000, allow_empty=True)

        # Refresh is a separate, host-owned credential lifecycle operation. It
        # must complete before the email side effect is dispatched; an ambiguous
        # refresh failure therefore blocks sending rather than risking an expired
        # credential or a duplicate provider request.
        if self._refresh is not None:
            try:
                self._refresh.refresh_if_needed(
                    owner_id=owner_id, account_id=account_id,
                )
            except Exception:
                raise GmailSendError("gmail_account_or_scope_unavailable") from None
        try:
            credential = self._credentials.resolve(
                owner_id=owner_id, provider_id=self._provider_id,
                account_id=account_id,
                required_scopes=("https://www.googleapis.com/auth/gmail.send",),
            )
        except OAuthProviderCredentialError:
            raise GmailSendError("gmail_account_or_scope_unavailable") from None
        message = EmailMessage(policy=policy.SMTP)
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message["Message-ID"] = message_id
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        payload = json.dumps({"raw": raw}, separators=(",", ":")).encode("utf-8")
        if len(payload) > self.MAX_REQUEST_BYTES:
            raise GmailSendError("gmail_message_too_large")

        request = Request(
            _GMAIL_API + "/gmail/v1/users/me/messages/send",
            data=payload,
            headers={
                "Authorization": "Bearer " + credential.access_token,
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with self._transport.open(request) as response:
                if response.status not in (200, 202):
                    raise GmailSendError("gmail_send_outcome_unknown")
                raw_response = response.read(self.MAX_RESPONSE_BYTES + 1)
                if (not isinstance(raw_response, bytes)
                        or len(raw_response) > self.MAX_RESPONSE_BYTES):
                    raise GmailSendError("gmail_send_outcome_unknown")
            result = json.loads(raw_response.decode("utf-8"))
        except GmailSendError:
            raise
        except Exception:
            # A timeout or disconnect can happen after Gmail accepted the message.
            raise GmailSendError("gmail_send_outcome_unknown") from None
        if (not isinstance(result, dict) or not isinstance(result.get("id"), str)
                or not _GMAIL_ID.fullmatch(result["id"])):
            raise GmailSendError("gmail_send_outcome_unknown")
        thread_id = result.get("threadId")
        if thread_id is not None and (
            not isinstance(thread_id, str) or not _GMAIL_ID.fullmatch(thread_id)
        ):
            raise GmailSendError("gmail_send_outcome_unknown")
        return {
            "provider_message_id": result["id"],
            "thread_id": thread_id,
            "message_id": message_id,
        }
