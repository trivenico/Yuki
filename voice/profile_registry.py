"""Consent-gated, metadata-only registry for user voice profiles.

Audio samples and model weights are deliberately managed by a separate provider
adapter. This registry stores only profile metadata and opaque artifact references.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


_PROFILE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_SCHEMA_VERSION = 1


class VoiceProfileRegistryError(RuntimeError):
    """Base error for safe voice-profile registry operations."""


class VoiceProfileAuthorizationError(VoiceProfileRegistryError):
    """Raised when the caller is not authorized to manage voice profiles."""


class VoiceProfileRegistry:
    """Persist staged/approved/active voice profile metadata for one user.

    The supplied authorization callback is checked on every read and mutation.
    This class never records audio, performs network access, or loads a model.
    """

    def __init__(self, directory: str | os.PathLike[str], *, authorize: Callable[[], bool]):
        if not callable(authorize):
            raise TypeError("An authorization callback is required.")
        self._authorize = authorize
        self._lock = threading.RLock()
        self._directory = Path(directory).expanduser()
        if self._directory.exists() and self._directory.is_symlink():
            raise VoiceProfileRegistryError("Voice profile directory must not be a symlink.")
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._directory.is_symlink() or not self._directory.is_dir():
            raise VoiceProfileRegistryError("Voice profile directory is not a safe directory.")
        try:
            os.chmod(self._directory, 0o700)
        except OSError as exc:
            raise VoiceProfileRegistryError("Could not secure voice profile directory permissions.") from exc
        self._path = self._directory / "profiles.json"
        if self._path.is_symlink():
            raise VoiceProfileRegistryError("Voice profile registry must not be a symlink.")
        self._state = self._load()

    def _require_authorized(self) -> None:
        try:
            authorized = self._authorize()
        except Exception as exc:
            raise VoiceProfileAuthorizationError("Voice profile authorization could not be verified.") from exc
        if authorized is not True:
            raise VoiceProfileAuthorizationError("An authenticated session is required to manage voice profiles.")

    def _load(self) -> dict:
        if not self._path.exists():
            return {"schema_version": _SCHEMA_VERSION, "profiles": {}, "active_id": None, "previous_id": None}
        if self._path.is_symlink() or not self._path.is_file():
            raise VoiceProfileRegistryError("Voice profile registry is not a regular file.")
        try:
            if self._path.stat().st_size > 256 * 1024:
                raise VoiceProfileRegistryError("Voice profile registry exceeds its size limit.")
            state = json.loads(self._path.read_text(encoding="utf-8"))
        except VoiceProfileRegistryError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise VoiceProfileRegistryError("Voice profile registry is unreadable or malformed.") from exc
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != _SCHEMA_VERSION
            or not isinstance(state.get("profiles"), dict)
            or state.get("active_id") is not None and not isinstance(state.get("active_id"), str)
            or state.get("previous_id") is not None and not isinstance(state.get("previous_id"), str)
        ):
            raise VoiceProfileRegistryError("Voice profile registry schema is invalid.")
        for key, record in state["profiles"].items():
            if not isinstance(key, str) or not _PROFILE_ID.fullmatch(key) or not isinstance(record, dict):
                raise VoiceProfileRegistryError("Voice profile registry contains an invalid profile.")
            if record.get("status") not in {"staged", "approved", "disabled"}:
                raise VoiceProfileRegistryError("Voice profile registry contains an invalid status.")
        active = state.get("active_id")
        if active is not None and active not in state["profiles"]:
            raise VoiceProfileRegistryError("Active voice profile is missing.")
        previous = state.get("previous_id")
        if previous is not None and previous not in state["profiles"]:
            raise VoiceProfileRegistryError("Previous voice profile is missing.")
        return state

    def _save(self) -> None:
        payload = json.dumps(self._state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fd, temporary = tempfile.mkstemp(prefix=".profiles-", suffix=".tmp", dir=self._directory)
        try:
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if self._path.is_symlink():
                raise VoiceProfileRegistryError("Voice profile registry must not be a symlink.")
            os.replace(temporary, self._path)
            try:
                os.chmod(self._path, 0o600)
            except OSError as exc:
                raise VoiceProfileRegistryError("Could not secure voice profile registry permissions.") from exc
        finally:
            try:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError:
                pass

    @staticmethod
    def _validate_text(value: str, label: str, maximum: int = 120) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{label} must be a non-empty string of at most {maximum} characters.")
        if any(ord(character) < 32 for character in value):
            raise ValueError(f"{label} contains unsupported control characters.")
        return value.strip()

    def list_profiles(self) -> list[dict]:
        self._require_authorized()
        with self._lock:
            return [dict(record, profile_id=key) for key, record in sorted(self._state["profiles"].items())]

    def active_profile(self) -> dict | None:
        self._require_authorized()
        with self._lock:
            key = self._state["active_id"]
            return dict(self._state["profiles"][key], profile_id=key) if key else None

    def stage_profile(
        self, profile_id: str, display_name: str, provider_id: str, artifact_ref: str,
        *, source_owner_consent: bool,
    ) -> dict:
        self._require_authorized()
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError("profile_id must contain only letters, numbers, underscores, or hyphens.")
        if source_owner_consent is not True:
            raise VoiceProfileAuthorizationError("Explicit source-owner consent is required before cloning.")
        record = {
            "display_name": self._validate_text(display_name, "display_name"),
            "provider_id": self._validate_text(provider_id, "provider_id", 80),
            "artifact_ref": self._validate_text(artifact_ref, "artifact_ref", 240),
            "status": "staged",
            "consent_confirmed_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            if profile_id in self._state["profiles"]:
                raise VoiceProfileRegistryError("A voice profile with this ID already exists.")
            self._state["profiles"][profile_id] = record
            try:
                self._save()
            except Exception:
                del self._state["profiles"][profile_id]
                raise
            return dict(record, profile_id=profile_id)

    def approve_profile(self, profile_id: str) -> dict:
        self._require_authorized()
        with self._lock:
            record = self._get(profile_id)
            if record["status"] != "staged":
                raise VoiceProfileRegistryError("Only a staged profile can be approved.")
            record["status"] = "approved"
            try:
                self._save()
            except Exception:
                record["status"] = "staged"
                raise
            return dict(record, profile_id=profile_id)

    def activate_profile(self, profile_id: str) -> dict:
        self._require_authorized()
        with self._lock:
            record = self._get(profile_id)
            if record["status"] != "approved":
                raise VoiceProfileRegistryError("Only an approved profile can become the default voice.")
            old_active = self._state["active_id"]
            old_previous = self._state["previous_id"]
            self._state["previous_id"] = old_active if old_active != profile_id else old_previous
            self._state["active_id"] = profile_id
            try:
                self._save()
            except Exception:
                self._state["active_id"] = old_active
                self._state["previous_id"] = old_previous
                raise
            return dict(record, profile_id=profile_id)

    def revert_to_previous(self) -> dict:
        self._require_authorized()
        with self._lock:
            previous = self._state["previous_id"]
            if previous is None:
                raise VoiceProfileRegistryError("There is no previous voice profile to restore.")
            record = self._get(previous)
            if record["status"] != "approved":
                raise VoiceProfileRegistryError("The previous voice profile is not available.")
            current = self._state["active_id"]
            self._state["active_id"] = previous
            self._state["previous_id"] = current
            try:
                self._save()
            except Exception:
                self._state["active_id"] = current
                self._state["previous_id"] = previous
                raise
            return dict(record, profile_id=previous)

    def disable_profile(self, profile_id: str) -> None:
        self._require_authorized()
        with self._lock:
            record = self._get(profile_id)
            if self._state["active_id"] == profile_id:
                raise VoiceProfileRegistryError("Restore another voice before disabling the active default.")
            old_status = record["status"]
            record["status"] = "disabled"
            try:
                self._save()
            except Exception:
                record["status"] = old_status
                raise

    def delete_profile(self, profile_id: str) -> None:
        self._require_authorized()
        with self._lock:
            record = self._get(profile_id)
            if profile_id in {self._state["active_id"], self._state["previous_id"]}:
                raise VoiceProfileRegistryError("Restore or clear the active/previous voice before deleting it.")
            del self._state["profiles"][profile_id]
            try:
                self._save()
            except Exception:
                self._state["profiles"][profile_id] = record
                raise

    def _get(self, profile_id: str) -> dict:
        if not isinstance(profile_id, str) or profile_id not in self._state["profiles"]:
            raise VoiceProfileRegistryError("Voice profile was not found.")
        return self._state["profiles"][profile_id]
