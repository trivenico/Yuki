"""Stable opaque Google account IDs derived only from verified OIDC identity.

Callers must pass the result returned by OIDCIdentityTokenVerifier.verify;
unverified JWT payloads, email addresses, and access-token contents are not
acceptable identity inputs.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

GOOGLE_OIDC_ISSUERS = frozenset({
    "https://accounts.google.com",
    "accounts.google.com",
})
GOOGLE_OIDC_ISSUER = "https://accounts.google.com"
GOOGLE_OIDC_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_OIDC_USERINFO_URI = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_OAUTH_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_GMAIL_API_ORIGIN = "https://gmail.googleapis.com"
GOOGLE_GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GOOGLE_GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GOOGLE_GMAIL_MINIMUM_SCOPES = (
    GOOGLE_GMAIL_READONLY_SCOPE,
    GOOGLE_GMAIL_SEND_SCOPE,
)
_SUBJECT = re.compile(r"^[A-Za-z0-9_-]{1,255}$")


class GoogleOAuthIdentityError(RuntimeError):
    """Stable, non-sensitive Google identity mapping failure."""


@dataclass(frozen=True)
class GoogleAccountIdentity:
    provider_id: str
    account_id: str
    issuer: str
    subject: str


class GoogleAccountIdentityFactory:
    """Map verified Google OIDC claims to a stable, non-PII account key."""

    provider_id = "google"

    def create(self, verified_identity: dict) -> GoogleAccountIdentity:
        if not isinstance(verified_identity, dict):
            raise GoogleOAuthIdentityError("google_identity_invalid")
        issuer = verified_identity.get("issuer")
        subject = verified_identity.get("subject")
        if issuer not in GOOGLE_OIDC_ISSUERS:
            raise GoogleOAuthIdentityError("google_issuer_invalid")
        if not isinstance(subject, str) or not _SUBJECT.fullmatch(subject):
            raise GoogleOAuthIdentityError("google_subject_invalid")
        # Email is mutable and may be reassigned; the OIDC subject is the stable
        # account identifier. Hash it so provider subject values aren't exposed
        # in connection URLs, logs, or ordinary connection metadata.
        digest = hashlib.sha256(
            (GOOGLE_OIDC_ISSUER + "\\0" + subject).encode("ascii")
        ).hexdigest()
        return GoogleAccountIdentity(
            provider_id=self.provider_id,
            account_id="google-" + digest,
            issuer=GOOGLE_OIDC_ISSUER,
            subject=subject,
        )
