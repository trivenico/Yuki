import unittest
from types import SimpleNamespace

from core.oauth_disconnect_service import OAuthDisconnectError, OAuthDisconnectService


class Vault:
    def __init__(self, credential=None, disconnect_result=True, disconnect_error=None):
        self.credential = credential
        self.disconnect_result = disconnect_result
        self.disconnect_error = disconnect_error
        self.deleted = False
    def get(self, **kwargs):
        return self.credential
    def disconnect(self, **kwargs):
        if self.disconnect_error:
            raise self.disconnect_error
        self.deleted = True
        return self.disconnect_result


class Transport:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
    def request_form_status(self, endpoint, *, fields):
        self.calls.append((endpoint, fields))
        if self.error:
            raise self.error
        return 200


class OAuthDisconnectServiceTests(unittest.TestCase):
    def credential(self):
        return SimpleNamespace(
            access_token="secret-access-token",
            refresh_token="secret-refresh-token",
        )

    def test_revokes_refresh_token_then_deletes_local_credentials(self):
        vault, transport = Vault(self.credential()), Transport()
        service = OAuthDisconnectService(
            credential_vault=vault, transport=transport,
            provider_id="provider", client_id="client",
            revocation_endpoint="https://auth.example.com/revoke",
        )
        result = service.disconnect(owner_id="owner", account_id="account")
        self.assertEqual(transport.calls[0][1]["token"], "secret-refresh-token")
        self.assertTrue(vault.deleted)
        self.assertTrue(result["remote_revoked"])
        self.assertTrue(result["disconnected"])
        self.assertNotIn("secret-refresh-token", repr(result))

    def test_remote_failure_still_deletes_local_credentials_without_retry(self):
        vault, transport = Vault(self.credential()), Transport(RuntimeError("secret failure"))
        service = OAuthDisconnectService(
            credential_vault=vault, transport=transport,
            provider_id="provider", client_id="client",
            revocation_endpoint="https://auth.example.com/revoke",
        )
        result = service.disconnect(owner_id="owner", account_id="account")
        self.assertEqual(len(transport.calls), 1)
        self.assertTrue(vault.deleted)
        self.assertFalse(result["remote_revoked"])
        self.assertTrue(result["disconnected"])

    def test_without_revocation_endpoint_still_disconnects_locally(self):
        vault = Vault(self.credential())
        service = OAuthDisconnectService(
            credential_vault=vault, transport=object(),
            provider_id="provider", client_id="client",
        )
        result = service.disconnect(owner_id="owner", account_id="account")
        self.assertTrue(result["disconnected"])
        self.assertFalse(result["revocation_attempted"])
        self.assertFalse(result["remote_revoked"])

    def test_missing_credential_still_clears_local_record(self):
        vault = Vault(None, disconnect_result=False)
        service = OAuthDisconnectService(
            credential_vault=vault, transport=object(),
            provider_id="provider", client_id="client",
        )
        result = service.disconnect(owner_id="owner", account_id="account")
        self.assertFalse(result["disconnected"])

    def test_local_delete_failure_is_sanitized(self):
        vault = Vault(self.credential(), disconnect_error=RuntimeError("secret"))
        service = OAuthDisconnectService(
            credential_vault=vault, transport=object(),
            provider_id="provider", client_id="client",
        )
        with self.assertRaises(OAuthDisconnectError) as caught:
            service.disconnect(owner_id="owner", account_id="account")
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
