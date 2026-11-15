"""Read-only Gmail message lookup over Yuki's pinned HTTPS transport.

This client deliberately has no send/delete/modify methods. It searches for a
preassigned RFC 822 Message-ID and verifies the returned message header before
reporting a match, making it suitable for later send-outcome reconciliation.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode
from urllib.request import Request

from core.oauth_provider_credentials import OAuthProviderCredentialResolver

_GMAIL_API = "https://gmail.googleapis.com"
_MESSAGE_ID = re.compile(r"^<[A-Za-z0-9._%+-]{1,180}@[A-Za-z0-9.-]{1,180}>$")
_GMAIL_ID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class GmailReadOnlyError(RuntimeError):
    """Stable, non-sensitive Gmail read failure."""


class GmailReadOnlyClient:
    MAX_RESPONSE_BYTES = 256_000

    def __init__(self, *, credential_resolver: OAuthProviderCredentialResolver,
                 transport, provider_id: str = "google"):
        if not isinstance(credential_resolver, OAuthProviderCredentialResolver):
            raise TypeError("an owner-scoped OAuth credential resolver is required")
        if not callable(getattr(transport, "open", None)):
            raise TypeError("a pinned HTTPS transport is required")
        if provider_id != "google":
            raise ValueError("Gmail client requires the trusted Google provider")
        if "gmail.googleapis.com" not in getattr(transport, "allowed_hosts", ()):
            raise ValueError("pinned transport must explicitly allow Gmail API host")
        self._credentials = credential_resolver
        self._transport = transport
        self._provider_id = provider_id

    @staticmethod
    def _message_id(value):
        if not isinstance(value, str) or not _MESSAGE_ID.fullmatch(value):
            raise GmailReadOnlyError("invalid_message_id")
        return value

    def _request_json(self, *, owner_id: str, account_id: str, path: str,
                      query: dict | None = None) -> dict:
        credential = self._credentials.resolve(
            owner_id=owner_id, provider_id=self._provider_id,
            account_id=account_id, required_scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        )
        url = _GMAIL_API + path
        if query:
            url += "?" + urlencode(query)
        request = Request(url, headers={
            "Authorization": "Bearer " + credential.access_token,
            "Accept": "application/json",
        }, method="GET")
        try:
            with self._transport.open(request) as response:
                if response.status != 200:
                    raise GmailReadOnlyError("gmail_api_request_rejected")
                raw = response.read(self.MAX_RESPONSE_BYTES + 1)
                if not isinstance(raw, bytes) or len(raw) > self.MAX_RESPONSE_BYTES:
                    raise GmailReadOnlyError("gmail_api_response_too_large")
            document = json.loads(raw.decode("utf-8"))
        except GmailReadOnlyError:
            raise
        except Exception:
            raise GmailReadOnlyError("gmail_api_unavailable") from None
        if not isinstance(document, dict):
            raise GmailReadOnlyError("invalid_gmail_api_response")
        return document

    def find_message_by_rfc822_message_id(
        self, *, owner_id: str, account_id: str, message_id: str,
    ) -> dict:
        """Return a minimal verified message reference, or an explicit not-found result."""
        message_id = self._message_id(message_id)
        listing = self._request_json(
            owner_id=owner_id, account_id=account_id,
            path="/gmail/v1/users/me/messages",
            query={"q": "rfc822msgid:" + message_id, "maxResults": "10"},
        )
        messages = listing.get("messages", [])
        if not isinstance(messages, list) or len(messages) > 10:
            raise GmailReadOnlyError("invalid_gmail_message_list")
        # A truncated result set cannot prove uniqueness. Never accept a
        # partial page as authoritative reconciliation evidence.
        if listing.get("nextPageToken") is not None:
            raise GmailReadOnlyError("ambiguous_gmail_message_match")
        matches = []
        for item in messages:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise GmailReadOnlyError("invalid_gmail_message_list")
            gmail_id = item["id"]
            if not _GMAIL_ID.fullmatch(gmail_id):
                raise GmailReadOnlyError("invalid_gmail_message_id")
            detail = self._request_json(
                owner_id=owner_id, account_id=account_id,
                path="/gmail/v1/users/me/messages/" + quote(gmail_id, safe=""),
                query={"format": "metadata", "metadataHeaders": "Message-ID"},
            )
            if detail.get("id") != gmail_id:
                raise GmailReadOnlyError("gmail_message_identity_mismatch")
            payload = detail.get("payload")
            if not isinstance(payload, dict):
                raise GmailReadOnlyError("invalid_gmail_message_metadata")
            headers = payload.get("headers", [])
            if not isinstance(headers, list) or len(headers) > 100:
                raise GmailReadOnlyError("invalid_gmail_message_metadata")
            found = []
            for header in headers:
                if not isinstance(header, dict):
                    raise GmailReadOnlyError("invalid_gmail_message_metadata")
                name = header.get("name")
                value = header.get("value")
                if not isinstance(name, str) or not isinstance(value, str):
                    raise GmailReadOnlyError("invalid_gmail_message_metadata")
                if name.lower() == "message-id":
                    found.append(value)
            thread_id = detail.get("threadId")
            if thread_id is not None and (
                not isinstance(thread_id, str) or not _GMAIL_ID.fullmatch(thread_id)
            ):
                raise GmailReadOnlyError("invalid_gmail_message_metadata")
            label_ids = detail.get("labelIds", [])
            if (not isinstance(label_ids, list) or len(label_ids) > 100
                    or any(not isinstance(label, str) or len(label) > 128 for label in label_ids)):
                raise GmailReadOnlyError("invalid_gmail_message_metadata")
            if len(found) == 1 and found[0] == message_id:
                matches.append({
                    "provider_message_id": gmail_id,
                    "thread_id": thread_id,
                    "message_id": message_id,
                    "label_ids": label_ids,
                })
        if len(matches) > 1:
            raise GmailReadOnlyError("ambiguous_gmail_message_match")
        return {"found": bool(matches), "message": matches[0] if matches else None}
