"""Provider identity binding through a trusted OAuth userinfo endpoint.

The verifier never decodes opaque access tokens or trusts unverified JWT claims.
Its endpoint and expected provider subject are host-owned configuration.
"""
from __future__ import annotations


class OAuthProviderIdentityError(RuntimeError):
    """Stable, non-sensitive provider identity verification failure."""


class OAuthProviderIdentityVerifier:
    def __init__(
        self, *, transport, userinfo_endpoint: str,
        expected_subject: str, expected_issuer: str,
        expected_audience: str | None = None,
    ):
        if not callable(getattr(transport, "request_json", None)):
            raise TypeError("a pinned OAuth JSON transport is required")
        for label, value in {
            "userinfo_endpoint": userinfo_endpoint,
            "expected_subject": expected_subject,
            "expected_issuer": expected_issuer,
        }.items():
            if not isinstance(value, str) or not value.strip() or len(value) > 2048:
                raise ValueError("trusted provider identity configuration is incomplete")
        if expected_audience is not None and (
            not isinstance(expected_audience, str) or not expected_audience.strip()
            or len(expected_audience) > 512
        ):
            raise ValueError("expected_audience is invalid")
        self._transport = transport
        self._endpoint = userinfo_endpoint
        self._subject = expected_subject
        self._issuer = expected_issuer
        self._audience = expected_audience

    def verify(self, *, access_token: str) -> dict:
        if not isinstance(access_token, str) or not access_token or len(access_token) > 8192:
            raise OAuthProviderIdentityError("provider_identity_credential_invalid")
        try:
            profile = self._transport.request_json(
                "GET", self._endpoint,
                headers={"Authorization": "Bearer " + access_token},
            )
        except Exception:
            raise OAuthProviderIdentityError("provider_identity_lookup_failed") from None
        if not isinstance(profile, dict):
            raise OAuthProviderIdentityError("provider_identity_response_invalid")
        subject = profile.get("sub")
        if not isinstance(subject, str) or subject != self._subject:
            raise OAuthProviderIdentityError("provider_account_identity_mismatch")
        issuer = profile.get("iss")
        if issuer is not None and issuer != self._issuer:
            raise OAuthProviderIdentityError("provider_identity_issuer_mismatch")
        if self._audience is not None:
            audience = profile.get("aud")
            audiences = [audience] if isinstance(audience, str) else audience
            if not isinstance(audiences, list) or self._audience not in audiences:
                raise OAuthProviderIdentityError("provider_identity_audience_mismatch")
        return {"subject": self._subject, "issuer": self._issuer}
