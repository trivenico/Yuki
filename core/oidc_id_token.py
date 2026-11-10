"""Cryptographic OIDC ID-token verification against trusted issuer JWKS.

Only asymmetric algorithms are accepted. Discovery/JWKS URLs, issuer, client ID,
and expected subject/nonce are host-owned values; token headers cannot select a
remote key URL or expand the trusted endpoint set.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


class OIDCIdentityTokenError(RuntimeError):
    """Stable, non-sensitive OIDC ID-token verification failure."""


_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_ALLOWED_ALGORITHMS = frozenset({"RS256", "PS256", "ES256", "EdDSA"})


def _decode_segment(value: str, *, maximum: int) -> bytes:
    if not isinstance(value, str) or not value or len(value) > maximum or not _B64URL.fullmatch(value):
        raise OIDCIdentityTokenError("oidc_token_encoding_invalid")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        raise OIDCIdentityTokenError("oidc_token_encoding_invalid") from None


def _json_object(raw: bytes, *, label: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise OIDCIdentityTokenError("oidc_" + label + "_invalid") from None
    if not isinstance(value, dict):
        raise OIDCIdentityTokenError("oidc_" + label + "_invalid")
    return value


def _integer(value, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise OIDCIdentityTokenError("oidc_" + label + "_invalid")
    return value


class OIDCIdentityTokenVerifier:
    """Verify OIDC signature and identity claims using a pinned JWKS transport."""

    def __init__(
        self, *, transport, jwks_uri: str, issuer: str, client_id: str,
        expected_subject: str, clock: Callable[[], float] = time.time,
        clock_skew_seconds: int = 60, maximum_token_age_seconds: int = 86_400,
    ):
        if not callable(getattr(transport, "request_json", None)):
            raise TypeError("a pinned OAuth JSON transport is required")
        for name, value, maximum in (
            ("jwks_uri", jwks_uri, 2048), ("issuer", issuer, 2048),
            ("client_id", client_id, 512), ("expected_subject", expected_subject, 512),
        ):
            if not isinstance(value, str) or not value or len(value) > maximum:
                raise ValueError("trusted OIDC configuration is incomplete")
        if type(clock_skew_seconds) is not int or not 0 <= clock_skew_seconds <= 300:
            raise ValueError("clock skew must be between 0 and 300 seconds")
        if type(maximum_token_age_seconds) is not int or not 1 <= maximum_token_age_seconds <= 604800:
            raise ValueError("maximum token age is invalid")
        self._transport = transport
        self._jwks_uri = jwks_uri
        self._issuer = issuer
        self._client_id = client_id
        self._subject = expected_subject
        self._clock = clock
        self._skew = clock_skew_seconds
        self._max_age = maximum_token_age_seconds

    @staticmethod
    def _public_key(jwk: dict, algorithm: str):
        if (jwk.get("use", "sig") != "sig" or jwk.get("alg", algorithm) != algorithm
                or any(name in jwk for name in ("d", "p", "q", "dp", "dq", "qi", "oth"))
                or ("key_ops" in jwk and jwk["key_ops"] != ["verify"])):
            raise OIDCIdentityTokenError("oidc_signing_key_invalid")
        if algorithm in ("RS256", "PS256"):
            if jwk.get("kty") != "RSA":
                raise OIDCIdentityTokenError("oidc_signing_key_invalid")
            n = int.from_bytes(_decode_segment(jwk.get("n"), maximum=2048), "big")
            e = int.from_bytes(_decode_segment(jwk.get("e"), maximum=16), "big")
            if n.bit_length() < 2048 or n.bit_length() > 8192 or e < 3 or e % 2 == 0:
                raise OIDCIdentityTokenError("oidc_signing_key_invalid")
            return rsa.RSAPublicNumbers(e, n).public_key()
        if algorithm == "ES256":
            if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
                raise OIDCIdentityTokenError("oidc_signing_key_invalid")
            x = int.from_bytes(_decode_segment(jwk.get("x"), maximum=128), "big")
            y = int.from_bytes(_decode_segment(jwk.get("y"), maximum=128), "big")
            return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
        if algorithm == "EdDSA":
            if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
                raise OIDCIdentityTokenError("oidc_signing_key_invalid")
            raw = _decode_segment(jwk.get("x"), maximum=128)
            if len(raw) != 32:
                raise OIDCIdentityTokenError("oidc_signing_key_invalid")
            return ed25519.Ed25519PublicKey.from_public_bytes(raw)
        raise OIDCIdentityTokenError("oidc_algorithm_not_allowed")

    @staticmethod
    def _verify_signature(key, algorithm: str, signed: bytes, signature: bytes):
        try:
            if algorithm == "RS256":
                key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
            elif algorithm == "PS256":
                key.verify(signature, signed, padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()), salt_length=32,
                ), hashes.SHA256())
            elif algorithm == "ES256":
                if len(signature) != 64:
                    raise InvalidSignature
                der = encode_dss_signature(
                    int.from_bytes(signature[:32], "big"),
                    int.from_bytes(signature[32:], "big"),
                )
                key.verify(der, signed, ec.ECDSA(hashes.SHA256()))
            elif algorithm == "EdDSA":
                key.verify(signature, signed)
            else:
                raise OIDCIdentityTokenError("oidc_algorithm_not_allowed")
        except (InvalidSignature, ValueError):
            raise OIDCIdentityTokenError("oidc_signature_invalid") from None

    def verify(
        self, *, id_token: str, expected_nonce: str, access_token: str | None = None,
    ) -> dict:
        if not isinstance(id_token, str) or len(id_token) > 32_768:
            raise OIDCIdentityTokenError("oidc_token_invalid")
        if not isinstance(expected_nonce, str) or not 16 <= len(expected_nonce) <= 512:
            raise OIDCIdentityTokenError("oidc_nonce_invalid")
        parts = id_token.split(".")
        if len(parts) != 3:
            raise OIDCIdentityTokenError("oidc_token_invalid")
        header = _json_object(_decode_segment(parts[0], maximum=8192), label="header")
        claims = _json_object(_decode_segment(parts[1], maximum=24576), label="claims")
        signature = _decode_segment(parts[2], maximum=2048)
        algorithm = header.get("alg")
        kid = header.get("kid")
        if algorithm not in _ALLOWED_ALGORITHMS or not isinstance(kid, str) or not 1 <= len(kid) <= 256:
            raise OIDCIdentityTokenError("oidc_algorithm_or_key_invalid")
        if (any(name in header for name in ("jku", "jwk", "x5u", "x5c", "crit", "b64"))
                or ("typ" in header and header["typ"] != "JWT")):
            raise OIDCIdentityTokenError("oidc_header_parameter_unsupported")
        try:
            jwks = self._transport.request_json("GET", self._jwks_uri)
        except Exception:
            raise OIDCIdentityTokenError("oidc_jwks_unavailable") from None
        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        if not isinstance(keys, list) or not 1 <= len(keys) <= 100:
            raise OIDCIdentityTokenError("oidc_jwks_invalid")
        kid_matches = [
            item for item in keys
            if isinstance(item, dict) and item.get("kid") == kid
        ]
        if len(kid_matches) != 1:
            raise OIDCIdentityTokenError("oidc_signing_key_not_found")
        matches = [
            item for item in kid_matches
            if item.get("kty") in ("RSA", "EC", "OKP")
        ]
        if len(matches) != 1:
            raise OIDCIdentityTokenError("oidc_signing_key_not_found")
        try:
            key = self._public_key(matches[0], algorithm)
        except OIDCIdentityTokenError:
            raise
        except Exception:
            raise OIDCIdentityTokenError("oidc_signing_key_invalid") from None
        self._verify_signature(key, algorithm, (parts[0] + "." + parts[1]).encode("ascii"), signature)

        now = int(self._clock())
        issuer = claims.get("iss")
        subject = claims.get("sub")
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        if issuer != self._issuer:
            raise OIDCIdentityTokenError("oidc_issuer_mismatch")
        if subject != self._subject:
            raise OIDCIdentityTokenError("oidc_subject_mismatch")
        if (not isinstance(audiences, list) or not audiences
                or not all(isinstance(item, str) and item for item in audiences)
                or len(set(audiences)) != len(audiences)
                or self._client_id not in audiences):
            raise OIDCIdentityTokenError("oidc_audience_mismatch")
        if len(audiences) > 1 and claims.get("azp") != self._client_id:
            raise OIDCIdentityTokenError("oidc_authorized_party_mismatch")
        if "azp" in claims and claims["azp"] != self._client_id:
            raise OIDCIdentityTokenError("oidc_authorized_party_mismatch")
        exp = _integer(claims.get("exp"), label="expiry")
        iat = _integer(claims.get("iat"), label="issued_at")
        if (exp <= now - self._skew or exp <= iat or iat > now + self._skew
                or exp - iat > self._max_age + (2 * self._skew)):
            raise OIDCIdentityTokenError("oidc_token_time_invalid")
        if now - iat > self._max_age + self._skew:
            raise OIDCIdentityTokenError("oidc_token_too_old")
        if "nbf" in claims and _integer(claims["nbf"], label="not_before") > now + self._skew:
            raise OIDCIdentityTokenError("oidc_token_not_yet_valid")
        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not hmac.compare_digest(
            nonce.encode("utf-8"), expected_nonce.encode("utf-8")
        ):
            raise OIDCIdentityTokenError("oidc_nonce_mismatch")
        if "at_hash" in claims:
            if not isinstance(access_token, str) or not access_token:
                raise OIDCIdentityTokenError("oidc_access_token_hash_unverifiable")
            try:
                access_bytes = access_token.encode("ascii")
            except UnicodeEncodeError:
                raise OIDCIdentityTokenError("oidc_access_token_hash_unverifiable") from None
            digest = (hashlib.sha512(access_bytes).digest() if algorithm == "EdDSA"
                      else hashlib.sha256(access_bytes).digest())
            half = len(digest) // 2
            expected_hash = base64.urlsafe_b64encode(digest[:half]).rstrip(b"=").decode("ascii")
            if not isinstance(claims["at_hash"], str) or not hmac.compare_digest(
                claims["at_hash"], expected_hash
            ):
                raise OIDCIdentityTokenError("oidc_access_token_hash_mismatch")
        return {"issuer": self._issuer, "subject": self._subject, "audience": self._client_id}
