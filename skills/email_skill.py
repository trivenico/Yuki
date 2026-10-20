"""Read-only Gmail search adapter using user-owned OAuth credentials.

This adapter never sends mail or changes labels. Gmail API packages and an OAuth
client file must be configured locally; credentials/token files must not be committed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class EmailSkillError(RuntimeError):
    pass


class GmailReadSkill:
    name = "gmail_search"
    description = "Search and read a small number of Gmail message snippets (read-only)."
    risk = "medium"
    requires_approval = True

    def __init__(
        self,
        *,
        enabled: bool = False,
        credentials_path: str | Path = "credentials.json",
        token_path: str | Path = "token.json",
    ):
        self.enabled = enabled
        self.credentials_path = Path(credentials_path).expanduser().resolve()
        self.token_path = Path(token_path).expanduser().resolve()
        self._service: Any | None = None

    def _connect(self):
        if not self.enabled:
            raise EmailSkillError("Gmail integration is disabled.")
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise EmailSkillError("Install the optional Google API packages first.") from exc
        scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        creds = (
            Credentials.from_authorized_user_file(str(self.token_path), scopes)
            if self.token_path.exists()
            else None
        )
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds or not creds.valid:
            if not self.credentials_path.is_file():
                raise EmailSkillError(
                    "Place your own Google OAuth desktop client JSON outside source control."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), scopes)
            creds = flow.run_local_server(port=0)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def execute(self, query: str = "newer_than:7d", *, limit: int = 10) -> dict:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20.")
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("Gmail query must be non-empty and bounded.")
        if self._service is None:
            self._connect()
        service = self._service
        if service is None:
            raise EmailSkillError("Gmail service could not be initialized.")
        try:
            result = (
                service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
            )
            messages = []
            for item in result.get("messages", []):
                message = (
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=item["id"],
                        format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    )
                    .execute()
                )
                headers = {
                    h["name"].lower(): h["value"]
                    for h in message.get("payload", {}).get("headers", [])
                }
                messages.append(
                    {
                        "id": message["id"],
                        "from": headers.get("from", ""),
                        "subject": headers.get("subject", ""),
                        "date": headers.get("date", ""),
                        "snippet": str(message.get("snippet", ""))[:1000],
                    }
                )
            return {"ok": True, "count": len(messages), "messages": messages}
        except Exception as exc:
            raise EmailSkillError("Gmail read request failed safely.") from exc
