"""Versioned, integrity-checked Yuki identity profile.

Identity is product configuration, not model consciousness or a security boundary.
Profile updates require explicit owner approval and optimistic revision matching.
The integrity digest detects accidental corruption; it is not an authentication
mechanism against an attacker who can rewrite the local file.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

from yuki_identity import (
    PRODUCT_NAME,
    DEFAULT_GREETING,
    DEFAULT_WAKE_WORD,
    VOICE_STYLE,
)

SCHEMA_VERSION = 1
_ALLOWED_FIELDS = frozenset({"display_name", "greeting", "voice_style", "interaction_style"})
_DEFAULT_PROFILE = {
    "display_name": PRODUCT_NAME,
    "greeting": DEFAULT_GREETING,
    "voice_style": VOICE_STYLE,
    "interaction_style": "helpful, calm, precise, proactive, privacy-first",
}


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_profile(profile: Any) -> dict:
    if not isinstance(profile, dict) or set(profile) != set(_DEFAULT_PROFILE):
        raise ValueError("Identity profile has an invalid field set.")
    result = {}
    for key, maximum in (("display_name", 80), ("greeting", 300),
                         ("voice_style", 160), ("interaction_style", 300)):
        value = profile.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{key} must contain 1-{maximum} characters.")
        result[key] = value.strip()
    if result["display_name"] != PRODUCT_NAME:
        raise ValueError("The canonical product identity cannot be renamed.")
    return result


class IdentityCore:
    """Owns a versioned local profile; never mutates prompts, policy or permissions."""

    def __init__(self, path: str | Path = "data/identity/yuki_identity_profile.json"):
        self.path = Path(path).expanduser().resolve()
        self._lock = RLock()

    @staticmethod
    def _envelope(revision: int, profile: dict) -> dict:
        payload = {"schema_version": SCHEMA_VERSION, "revision": revision, "profile": profile}
        return {**payload, "sha256": hashlib.sha256(_canonical(payload)).hexdigest()}

    def _read(self) -> tuple[int, dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0, dict(_DEFAULT_PROFILE)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Identity profile is unreadable; no data was overwritten.") from exc
        if not isinstance(raw, dict):
            raise RuntimeError("Identity profile envelope is invalid.")
        digest = raw.get("sha256")
        payload = {key: raw.get(key) for key in ("schema_version", "revision", "profile")}
        if not isinstance(digest, str) or not hmac.compare_digest(
            digest, hashlib.sha256(_canonical(payload)).hexdigest()
        ):
            raise RuntimeError("Identity profile integrity check failed.")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise RuntimeError("Unsupported identity profile schema version.")
        revision = payload["revision"]
        if type(revision) is not int or revision < 1:
            raise RuntimeError("Identity profile revision is invalid.")
        try:
            profile = _validate_profile(payload["profile"])
        except ValueError as exc:
            raise RuntimeError("Identity profile contents are invalid.") from exc
        return revision, profile

    def snapshot(self) -> dict:
        with self._lock:
            revision, profile = self._read()
            return {
                "product_name": PRODUCT_NAME,
                "wake_word": DEFAULT_WAKE_WORD,
                "revision": revision,
                "schema_version": SCHEMA_VERSION,
                "profile": dict(profile),
                "authority": "configuration_only",
                "may_change_security_policy": False,
                "may_grant_tool_permissions": False,
            }

    def update(self, changes: dict, *, expected_revision: int, approved: bool = False) -> dict:
        if approved is not True:
            raise PermissionError("explicit_identity_change_approval_required")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer.")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a non-empty object.")
        unknown = set(changes) - _ALLOWED_FIELDS
        if unknown:
            raise ValueError("Unsupported identity fields: " + ", ".join(sorted(unknown)))
        with self._lock:
            revision, current = self._read()
            if revision != expected_revision:
                raise RuntimeError("identity_revision_conflict")
            candidate = dict(current)
            candidate.update(changes)
            profile = _validate_profile(candidate)
            self._write(self._envelope(revision + 1, profile))
            return self.snapshot()

    def reset(self, *, expected_revision: int, approved: bool = False) -> dict:
        if approved is not True:
            raise PermissionError("explicit_identity_change_approval_required")
        with self._lock:
            revision, _ = self._read()
            if revision != expected_revision:
                raise RuntimeError("identity_revision_conflict")
            self._write(self._envelope(revision + 1, dict(_DEFAULT_PROFILE)))
            return self.snapshot()

    def _write(self, envelope: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".yuki-identity-", dir=str(self.path.parent))
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(envelope, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            try:
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
