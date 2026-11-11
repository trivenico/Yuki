import base64
import hashlib
import json
import time
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.oidc_id_token import OIDCIdentityTokenError, OIDCIdentityTokenVerifier


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def segment(value):
    return b64(json.dumps(value, separators=(",", ":")).encode("utf-8"))


class FakeTransport:
    def __init__(self, document):
        self.document = document
        self.calls = []

    def request_json(self, method, endpoint):
        self.calls.append((method, endpoint))
        if method != "GET":
            raise AssertionError("JWKS must be read-only")
        return self.document


class OIDCIdentityTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = cls.private_key.public_key().public_numbers()
        cls.jwk = {
            "kty": "RSA",
            "kid": "key-1",
            "use": "sig",
            "alg": "RS256",
            "n": b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
            "e": b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
        }

    def setUp(self):
        self.now = int(time.time())
        self.transport = FakeTransport({"keys": [self.jwk]})
        self.verifier = OIDCIdentityTokenVerifier(
            transport=self.transport,
            jwks_uri="https://auth.example.com/.well-known/jwks.json",
            issuer="https://auth.example.com",
            client_id="yuki-client",
            expected_subject="provider-user-123",
            clock=lambda: self.now,
        )

    def token(self, **overrides):
        header = {"alg": "RS256", "kid": "key-1", "typ": "JWT"}
        claims = {
            "iss": "https://auth.example.com",
            "sub": "provider-user-123",
            "aud": "yuki-client",
            "iat": self.now,
            "exp": self.now + 300,
            "nonce": "nonce-value-1234567890",
        }
        claims.update(overrides)
        signing_input = (segment(header) + "." + segment(claims)).encode("ascii")
        signature = self.private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return signing_input.decode("ascii") + "." + b64(signature)

    def test_verifies_signature_and_required_identity_claims(self):
        result = self.verifier.verify(
            id_token=self.token(), expected_nonce="nonce-value-1234567890",
        )
        self.assertEqual(result, {
            "issuer": "https://auth.example.com",
            "subject": "provider-user-123",
            "audience": "yuki-client",
        })
        self.assertEqual(self.transport.calls, [
            ("GET", "https://auth.example.com/.well-known/jwks.json")
        ])
        access_token = "opaque-access-token"
        at_hash = b64(hashlib.sha256(access_token.encode("ascii")).digest()[:16])
        self.verifier.verify(
            id_token=self.token(at_hash=at_hash),
            expected_nonce="nonce-value-1234567890",
            access_token=access_token,
        )
        with self.assertRaisesRegex(OIDCIdentityTokenError, "access_token_hash_mismatch"):
            self.verifier.verify(
                id_token=self.token(at_hash="wrong-hash"),
                expected_nonce="nonce-value-1234567890",
                access_token=access_token,
            )

    def test_verifies_ps256_es256_and_eddsa(self):
        nonce = "nonce-value-1234567890"

        ps_header = segment({"alg": "PS256", "kid": "key-1"})
        ps_claims = segment({
            "iss": "https://auth.example.com", "sub": "provider-user-123",
            "aud": "yuki-client", "iat": self.now, "exp": self.now + 300, "nonce": nonce,
        })
        ps_input = (ps_header + "." + ps_claims).encode("ascii")
        ps_sig = self.private_key.sign(
            ps_input,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        ps_verifier = OIDCIdentityTokenVerifier(
            transport=FakeTransport({"keys": [{**self.jwk, "alg": "PS256"}]}),
            jwks_uri="https://auth.example.com/.well-known/jwks.json",
            issuer="https://auth.example.com", client_id="yuki-client",
            expected_subject="provider-user-123", clock=lambda: self.now,
        )
        self.assertEqual(
            ps_verifier.verify(
                id_token=ps_header + "." + ps_claims + "." + b64(ps_sig),
                expected_nonce=nonce,
            )["subject"],
            "provider-user-123",
        )

        ec_private = ec.generate_private_key(ec.SECP256R1())
        ec_numbers = ec_private.public_key().public_numbers()
        ec_jwk = {
            "kty": "EC", "crv": "P-256", "kid": "ec-1", "use": "sig", "alg": "ES256",
            "x": b64(ec_numbers.x.to_bytes(32, "big")),
            "y": b64(ec_numbers.y.to_bytes(32, "big")),
        }
        ec_verifier = OIDCIdentityTokenVerifier(
            transport=FakeTransport({"keys": [ec_jwk]}),
            jwks_uri="https://auth.example.com/.well-known/jwks.json",
            issuer="https://auth.example.com", client_id="yuki-client",
            expected_subject="provider-user-123", clock=lambda: self.now,
        )
        ec_header = segment({"alg": "ES256", "kid": "ec-1"})
        ec_claims = segment({
            "iss": "https://auth.example.com", "sub": "provider-user-123",
            "aud": "yuki-client", "iat": self.now, "exp": self.now + 300, "nonce": nonce,
        })
        ec_input = (ec_header + "." + ec_claims).encode("ascii")
        der = ec_private.sign(ec_input, ec.ECDSA(hashes.SHA256()))
        r_value, s_value = decode_dss_signature(der)
        ec_signature = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
        self.assertEqual(
            ec_verifier.verify(
                id_token=ec_header + "." + ec_claims + "." + b64(ec_signature),
                expected_nonce=nonce,
            )["subject"],
            "provider-user-123",
        )

        ed_private = ed25519.Ed25519PrivateKey.generate()
        ed_public = ed_private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ed_jwk = {"kty": "OKP", "crv": "Ed25519", "kid": "ed-1", "use": "sig",
                  "alg": "EdDSA", "x": b64(ed_public)}
        ed_verifier = OIDCIdentityTokenVerifier(
            transport=FakeTransport({"keys": [ed_jwk]}),
            jwks_uri="https://auth.example.com/.well-known/jwks.json",
            issuer="https://auth.example.com", client_id="yuki-client",
            expected_subject="provider-user-123", clock=lambda: self.now,
        )
        ed_header = segment({"alg": "EdDSA", "kid": "ed-1"})
        ed_claims = segment({
            "iss": "https://auth.example.com", "sub": "provider-user-123",
            "aud": "yuki-client", "iat": self.now, "exp": self.now + 300, "nonce": nonce,
        })
        ed_input = (ed_header + "." + ed_claims).encode("ascii")
        ed_signature = ed_private.sign(ed_input)
        self.assertEqual(
            ed_verifier.verify(
                id_token=ed_header + "." + ed_claims + "." + b64(ed_signature),
                expected_nonce=nonce,
            )["subject"],
            "provider-user-123",
        )

    def test_rejects_wrong_nonce_issuer_subject_audience_and_expiry(self):
        invalid = (
            (self.token(), "wrong-nonce-1234567890"),
            (self.token(iss="https://evil.example"), "nonce-value-1234567890"),
            (self.token(sub="other-user"), "nonce-value-1234567890"),
            (self.token(aud="other-client"), "nonce-value-1234567890"),
            (self.token(exp=self.now), "nonce-value-1234567890"),
        )
        for token, nonce in invalid:
            with self.subTest(nonce=nonce), self.assertRaises(OIDCIdentityTokenError):
                self.verifier.verify(id_token=token, expected_nonce=nonce)

    def test_rejects_invalid_signature_and_untrusted_key_url_header(self):
        token = self.token()
        parts = token.split(".")
        changed = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        bad_signature = parts[0] + "." + parts[1] + "." + changed
        with self.assertRaisesRegex(OIDCIdentityTokenError, "signature_invalid"):
            self.verifier.verify(id_token=bad_signature, expected_nonce="nonce-value-1234567890")
        header = segment({"alg": "RS256", "kid": "key-1", "jku": "https://evil.example/jwks"})
        signed = (header + "." + parts[1]).encode("ascii")
        signature = self.private_key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        with self.assertRaisesRegex(OIDCIdentityTokenError, "header_parameter_unsupported"):
            self.verifier.verify(
                id_token=header + "." + parts[1] + "." + b64(signature),
                expected_nonce="nonce-value-1234567890",
            )

    def test_rejects_duplicate_kids_and_private_jwks_material(self):
        self.transport.document = {"keys": [self.jwk, dict(self.jwk)]}
        self.verifier._jwks_cache.invalidate(self.verifier._jwks_uri)
        with self.assertRaisesRegex(OIDCIdentityTokenError, "signing_key_not_found"):
            self.verifier.verify(id_token=self.token(), expected_nonce="nonce-value-1234567890")
        self.transport.document = {"keys": [self.jwk, {"kid": "key-1", "kty": "oct"}]}
        self.verifier._jwks_cache.invalidate(self.verifier._jwks_uri)
        with self.assertRaisesRegex(OIDCIdentityTokenError, "signing_key_not_found"):
            self.verifier.verify(id_token=self.token(), expected_nonce="nonce-value-1234567890")
        self.transport.document = {"keys": [{**self.jwk, "d": "private"}]}
        self.verifier._jwks_cache.invalidate(self.verifier._jwks_uri)
        with self.assertRaisesRegex(OIDCIdentityTokenError, "signing_key_invalid"):
            self.verifier.verify(id_token=self.token(), expected_nonce="nonce-value-1234567890")

    def test_rejects_multiple_audiences_without_authorized_party(self):
        with self.assertRaisesRegex(OIDCIdentityTokenError, "authorized_party_mismatch"):
            self.verifier.verify(
                id_token=self.token(aud=["yuki-client", "another-client"]),
                expected_nonce="nonce-value-1234567890",
            )

    def test_rejects_malformed_compact_token_and_bad_jwks(self):
        with self.assertRaises(OIDCIdentityTokenError):
            self.verifier.verify(id_token="not.a.jwt", expected_nonce="nonce-value-1234567890")
        self.transport.document = {"keys": []}
        self.verifier._jwks_cache.invalidate(self.verifier._jwks_uri)
        with self.assertRaisesRegex(OIDCIdentityTokenError, "jwks_invalid"):
            self.verifier.verify(id_token=self.token(), expected_nonce="nonce-value-1234567890")


if __name__ == "__main__":
    unittest.main()
