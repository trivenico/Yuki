"""Consent-gated local registry for user-approved cloned voice profiles.

This module manages metadata only. It does not record audio, clone voices,
download models, or synthesize speech. A future clone engine must create its
model directory under the registry root before registering it.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SOURCE_KINDS = frozenset({"user_owned", "authorized", "licensed", "synthetic"})


class VoiceProfileRegistryError(RuntimeError):
    """A voice profile could not be safely loaded or changed."""


@dataclass(frozen=True)
class ClonedVoiceProfile:
    profile_id: str
    display_name: str
    engine: str
    model_directory: str
    source_kind: str


class VoiceProfileRegistry:
    """Store approved clone metadata and the selected default in a private folder.

    The source recording is never accepted or persisted by this registry.
    Consent is an explicit caller assertion, not a legal determination; the UI
    must explain source rights and obtain confirmation before calling add().
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._manifest = self.root / "profiles.json"
        self._apply_private_permissions(self.root, directory=True)

    @staticmethod
    def _apply_private_permissions(path: Path, *, directory: bool = False) -> None:
        if os.name == "nt":
            # Windows ACLs are inherited from the user's profile directory.
            # Production UI must surface that the directory is user-private.
            return
        try:
            path.chmod(0o700 if directory else 0o600)
        except OSError as exc:
            raise VoiceProfileRegistryError("Voice profile storage permissions could not be restricted.") from exc

    def _read(self) -> dict:
        if not self._manifest.exists():
            return {"version": 1, "default_profile_id": None, "profiles": []}
        try:
            data = json.loads(self._manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise VoiceProfileRegistryError("Voice profile registry is unreadable; no changes were made.") from exc
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("profiles"), list):
            raise VoiceProfileRegistryError("Voice profile registry has an unsupported format.")
        ids = set()
        for item in data["profiles"]:
            if not isinstance(item, dict) or not isinstance(item.get("profile_id"), str):
                raise VoiceProfileRegistryError("Voice profile registry contains an invalid entry.")
            if item["profile_id"] in ids:
                raise VoiceProfileRegistryError("Voice profile registry contains duplicate entries.")
            ids.add(item["profile_id"])
        default_id = data.get("default_profile_id")
        if default_id is not None and default_id not in ids:
            raise VoiceProfileRegistryError("Default voice profile does not exist in the registry.")
        return data

    def _write(self, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=".profiles-", suffix=".tmp", dir=self.root)
        temp_path = Path(temporary)
        try:
            self._apply_private_permissions(temp_path)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self._manifest)
            self._apply_private_permissions(self._manifest)
            if os.name != "nt":
                directory_fd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def list_profiles(self) -> tuple[ClonedVoiceProfile, ...]:
        with self._lock:
            data = self._read()
            return tuple(ClonedVoiceProfile(**item) for item in data["profiles"])

    def default_profile_id(self) -> str | None:
        with self._lock:
            return self._read()["default_profile_id"]

    def add(
        self,
        *,
        profile_id: str,
        display_name: str,
        engine: str,
        model_directory: str | Path,
        source_kind: str,
        source_authorized: bool,
    ) -> ClonedVoiceProfile:
        """Register a completed local clone after explicit source-rights approval."""
        if not isinstance(source_authorized, bool) or source_authorized is not True:
            raise VoiceProfileRegistryError("Explicit authorization for the source voice is required.")
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError("profile_id must be a lowercase slug of at most 64 characters.")
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 80:
            raise ValueError("display_name must contain 1-80 characters.")
        if not isinstance(engine, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", engine):
            raise ValueError("engine must be a lowercase engine identifier.")
        if source_kind not in _SOURCE_KINDS:
            raise ValueError("source_kind must identify an owned, authorized, licensed, or synthetic voice.")
        candidate = Path(model_directory).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=True)
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise VoiceProfileRegistryError("Cloned voice models must be stored inside the private profile directory.") from exc
        if not resolved.is_dir() or resolved == self.root:
            raise ValueError("model_directory must be an existing profile subdirectory.")
        profile = ClonedVoiceProfile(profile_id, display_name.strip(), engine, relative.as_posix(), source_kind)
        with self._lock:
            data = self._read()
            if any(item["profile_id"] == profile_id for item in data["profiles"]):
                raise VoiceProfileRegistryError("A voice profile with this identifier already exists.")
            data["profiles"].append(asdict(profile))
            self._write(data)
        return profile

    def set_default(self, profile_id: str) -> None:
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError("Invalid voice profile identifier.")
        with self._lock:
            data = self._read()
            if not any(item["profile_id"] == profile_id for item in data["profiles"]):
                raise VoiceProfileRegistryError("Cannot select a voice profile that is not registered.")
            data["default_profile_id"] = profile_id
            self._write(data)

    def remove(self, profile_id: str) -> None:
        """Remove registry metadata only; model deletion is a separate confirmed action."""
        with self._lock:
            data = self._read()
            remaining = [item for item in data["profiles"] if item["profile_id"] != profile_id]
            if len(remaining) == len(data["profiles"]):
                raise VoiceProfileRegistryError("Voice profile was not found.")
            if data["default_profile_id"] == profile_id:
                data["default_profile_id"] = None
            data["profiles"] = remaining
            self._write(data)
