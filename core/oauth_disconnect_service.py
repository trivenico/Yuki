"""Owner-initiated OAuth revocation and local credential disconnect.

Remote revocation is single-attempt. Local credential deletion is performed even
when the provider is unreachable, so a failed remote call never keeps a usable
local bearer token available to Yuki.
"""
from __future__ import annotations


class OAuthDisconnectError(RuntimeError):
    """Stable, non-sensitive disconnect failure."""


class OAuthDisconnectService:
    def __init__(
        self, *, credential_vault, transport, provider_id: str,
        client_id: str, revocation_endpoint: str | None = None,
    ):
        if not callable(getattr(credential_vault, "get", None)) or not callable(
            getattr(credential_vault, "disconnect", None)
        ):
            raise TypeError("an encrypted credential vault with disconnect support is required")
        if revocation_endpoint is not None and not callable(
            getattr(transport, "request_form_status", None)
        ):
            raise TypeError("a pinned OAuth revocation transport is required")
        for label, value in {"provider_id": provider_id, "client_id": client_id}.items():
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError("trusted OAuth disconnect configuration is incomplete")
        if revocation_endpoint is not None and (
            not isinstance(revocation_endpoint, str) or not revocation_endpoint.strip()
            or len(revocation_endpoint) > 2048
        ):
            raise ValueError("revocation_endpoint is invalid")
        self._vault = credential_vault
        self._transport = transport
        self._provider_id = provider_id
        self._client_id = client_id
        self._revocation_endpoint = revocation_endpoint

    def disconnect(self, *, owner_id: str, account_id: str) -> dict:
        try:
            credential = self._vault.get(
                owner_id=owner_id, provider_id=self._provider_id, account_id=account_id,
            )
        except Exception:
            raise OAuthDisconnectError("credential_lookup_failed") from None

        remote_revoked = False
        revocation_attempted = False
        if credential is not None and self._revocation_endpoint is not None:
            revocation_attempted = True
            try:
                self._transport.request_form_status(
                    self._revocation_endpoint,
                    fields={
                        "token": credential.refresh_token or credential.access_token,
                        "token_type_hint": "refresh_token" if credential.refresh_token else "access_token",
                        "client_id": self._client_id,
                    },
                )
                remote_revoked = True
            except Exception:
                # Do not retry an ambiguous revocation request. Local deletion
                # below is still mandatory and does not depend on remote success.
                remote_revoked = False

        try:
            disconnected = self._vault.disconnect(
                owner_id=owner_id, provider_id=self._provider_id, account_id=account_id,
            )
        except Exception:
            raise OAuthDisconnectError("local_credential_disconnect_failed") from None

        return {
            "provider_id": self._provider_id,
            "account_id": account_id,
            "disconnected": bool(disconnected),
            "revocation_attempted": revocation_attempted,
            "remote_revoked": remote_revoked,
        }
