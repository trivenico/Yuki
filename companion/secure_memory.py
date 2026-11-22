"""Optional AES-256-GCM encrypted local companion memory.

The encryption key is stored in the operating-system keyring, never beside the
ciphertext. This backend deliberately fails closed when a secure keyring is not
available; it never silently falls back to plaintext.
"""
from __future__ import annotations

import base64
import json
import os
import stat
import hmac
import hashlib
import tempfile
from pathlib import Path

from companion.memory import MemoryStore
from core.json_file_store import file_lock

_SERVICE = "yuki-companion-memory"
_ACCOUNT = "default"
_AAD = b"yuki-companion-memory:v1"


def _keyring_lock_path(account: str) -> Path:
    """Return a cross-process lock path without exposing account identifiers."""
    digest = hashlib.sha256(account.encode("utf-8")).hexdigest()
    return Path.home() / ".yuki" / "memory-keyring" / digest


class EncryptedMemoryStore(MemoryStore):
    VERSION = 1
    MAX_FILE_BYTES = 10 * 1024 * 1024

    def __init__(
        self, path: str | Path = "yuki_memory.enc.json", *,
        keyring_module=None, aesgcm_class=None, keyring_account: str = _ACCOUNT,
    ):
        super().__init__(path)
        if not isinstance(keyring_account, str) or not keyring_account.strip() or len(keyring_account) > 128:
            raise ValueError("keyring_account must be non-empty bounded text.")
        self._keyring_account = keyring_account
        try:
            if keyring_module is None:
                import keyring as keyring_module
            if aesgcm_class is None:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM as aesgcm_class
        except ImportError as exc:
            raise RuntimeError(
                "Encrypted memory dependencies are missing; install requirements-memory-security.txt."
            ) from exc
        try:
            backend = keyring_module.get_keyring()
            priority = getattr(backend, "priority", 0)
            if not isinstance(priority, (int, float)) or priority <= 0:
                raise RuntimeError("No secure operating-system keyring backend is available.")
        except Exception as exc:
            raise RuntimeError("A secure operating-system keyring is required for encrypted memory.") from exc
        self._keyring = keyring_module
        self._aesgcm = aesgcm_class
        self._key = self._load_or_create_key()

    def _load_or_create_key(self) -> bytes:
        try:
            # Serialize read/create/verify across every Yuki process using this
            # account; concurrent first-run stores must share one DEK.
            with file_lock(_keyring_lock_path(self._keyring_account)):
                encoded = self._keyring.get_password(_SERVICE, self._keyring_account)
                # Account-scoped stores never inherit the legacy shared-profile key.
                if encoded is None and self.path.exists():
                    raise RuntimeError("An existing encrypted memory file has no keyring key.")
                if encoded is None:
                    key = os.urandom(32)
                    encoded = base64.b64encode(key).decode("ascii")
                    self._keyring.set_password(_SERVICE, self._keyring_account, encoded)
                    if self._keyring.get_password(_SERVICE, self._keyring_account) != encoded:
                        raise RuntimeError("Keyring did not retain the newly created memory key.")
                key = base64.b64decode(encoded, validate=True)
                if len(key) != 32:
                    raise ValueError("invalid key length")
                return key
        except Exception as exc:
            raise RuntimeError("Could not safely access the Yuki memory encryption key.") from exc

    def export_key_for_encrypted_backup(
        self, profile_id: str, *, actor_id: str | None = None,
    ) -> bytes:
        """Export profile-legacy or authenticated-account key metadata for encrypted archives."""
        from core.profile_identity import (
            account_memory_keyring_account, profile_memory_keyring_account,
            profile_memory_namespace,
        )
        if actor_id is None:
            if self._keyring_account != profile_memory_keyring_account(profile_id):
                raise RuntimeError("Memory key does not belong to the requested profile.")
            payload = {
                "schema_version": 1,
                "profile_id": profile_id,
                "key": base64.b64encode(self._key).decode("ascii"),
            }
        else:
            namespace = profile_memory_namespace(profile_id, actor_id)
            if self._keyring_account != account_memory_keyring_account(profile_id, actor_id):
                raise RuntimeError("Memory key does not belong to the authenticated account.")
            payload = {
                "schema_version": 2,
                "profile_id": profile_id,
                "account_namespace": namespace,
                "key": base64.b64encode(self._key).decode("ascii"),
            }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def import_key_from_encrypted_backup(
        payload: bytes, profile_id: str, *, keyring_module, memory_path: str | Path,
        actor_id: str | None = None,
    ) -> bool:
        """Install a verified backup DEK in the OS keyring; return whether a key was created."""
        from core.profile_identity import profile_memory_keyring_account
        if not isinstance(payload, bytes) or len(payload) > 2048:
            raise RuntimeError("Portable memory key metadata is invalid.")
        try:
            metadata = json.loads(payload.decode("utf-8"))
            from core.profile_identity import (
                account_memory_keyring_account, profile_memory_keyring_account,
                profile_memory_namespace,
            )
            if actor_id is None:
                if (
                    not isinstance(metadata, dict)
                    or set(metadata) != {"schema_version", "profile_id", "key"}
                    or metadata.get("schema_version") != 1
                    or metadata.get("profile_id") != profile_id
                    or not isinstance(metadata.get("key"), str)
                ):
                    raise ValueError("invalid legacy portable key metadata")
                account = profile_memory_keyring_account(profile_id)
            else:
                expected_namespace = profile_memory_namespace(profile_id, actor_id)
                if (
                    not isinstance(metadata, dict)
                    or set(metadata) != {"schema_version", "profile_id", "account_namespace", "key"}
                    or metadata.get("schema_version") != 2
                    or metadata.get("profile_id") != profile_id
                    or not isinstance(metadata.get("account_namespace"), str)
                    or not hmac.compare_digest(metadata["account_namespace"], expected_namespace)
                    or not isinstance(metadata.get("key"), str)
                ):
                    raise ValueError("account portable key identity mismatch")
                account = account_memory_keyring_account(profile_id, actor_id)
            key = base64.b64decode(metadata["key"], validate=True)
            if len(key) != 32:
                raise ValueError("invalid portable memory key length")
            memory_file = Path(memory_path).expanduser()
            # Bind validation and reading to the same descriptor. A separate
            # lstat()+read_text() sequence could follow a symlink swapped in
            # between the check and the read.
            info = memory_file.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ValueError("encrypted memory file must not be a symbolic link")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(memory_file, flags)
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or not stat.S_ISREG(opened.st_mode)
                    or opened.st_dev != info.st_dev
                    or opened.st_ino != info.st_ino
                    or opened.st_nlink != 1
                    or opened.st_size <= 0
                    or opened.st_size > EncryptedMemoryStore.MAX_FILE_BYTES
                ):
                    raise ValueError("invalid encrypted memory file")
                chunks = []
                remaining = EncryptedMemoryStore.MAX_FILE_BYTES + 1
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                if not raw or len(raw) > EncryptedMemoryStore.MAX_FILE_BYTES:
                    raise ValueError("invalid encrypted memory file size")
            finally:
                os.close(descriptor)
            envelope = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(envelope, dict)
                or envelope.get("version") != EncryptedMemoryStore.VERSION
                or not isinstance(envelope.get("nonce"), str)
                or not isinstance(envelope.get("ciphertext"), str)
            ):
                raise ValueError("invalid encrypted memory envelope")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            if len(nonce) != 12 or len(ciphertext) < 16:
                raise ValueError("invalid encrypted memory payload")
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, _AAD)
            entries = json.loads(plaintext.decode("utf-8"))
            EncryptedMemoryStore._validate_entries(entries)
            backend = keyring_module.get_keyring()
            priority = getattr(backend, "priority", 0)
            if not isinstance(priority, (int, float)) or priority <= 0:
                raise ValueError("secure OS keyring unavailable")
            encoded = base64.b64encode(key).decode("ascii")
            existing = keyring_module.get_password(_SERVICE, account)
            if existing is not None:
                existing_key = base64.b64decode(existing, validate=True)
                if len(existing_key) != 32 or not hmac.compare_digest(existing_key, key):
                    raise ValueError("destination profile already has a different memory key")
                return False
            try:
                keyring_module.set_password(_SERVICE, account, encoded)
                stored = keyring_module.get_password(_SERVICE, account)
                if stored != encoded:
                    raise ValueError("keyring did not persist the imported memory key")
            except Exception:
                # set_password may have committed before reporting an error.
                # Remove only the exact value this operation attempted to add.
                try:
                    if keyring_module.get_password(_SERVICE, account) == encoded:
                        keyring_module.delete_password(_SERVICE, account)
                except Exception:
                    pass
                raise
            return True
        except Exception as exc:
            raise RuntimeError("Could not safely import the encrypted-memory key into the OS keyring.") from exc

    @staticmethod
    def _validate_entries(entries: object) -> list[dict]:
        """Apply the same bounded entry contract before accepting decrypted data."""
        if not isinstance(entries, list) or len(entries) > MemoryStore.MAX_ENTRIES:
            raise ValueError("Memory payload has an invalid structure or exceeds limits.")
        validated = []
        for item in entries:
            if not isinstance(item, dict) or not all(
                key in item for key in ("id", "created_at", "text")
            ):
                raise ValueError("Memory payload contains an invalid entry.")
            if (
                not isinstance(item["id"], str) or not item["id"]
                or not isinstance(item["created_at"], str) or not item["created_at"]
                or not isinstance(item["text"], str)
                or not item["text"].strip() or len(item["text"]) > MemoryStore.MAX_TEXT
            ):
                raise ValueError("Memory payload contains invalid bounded fields.")
            category = item.get("category", "note")
            if category not in {"note", "preference", "lesson"}:
                raise ValueError("Memory payload contains an invalid category.")
            validated.append(dict(item))
        return validated

    def _load(self) -> list[dict]:
        try:
            # Do not follow a swapped symlink or read a multiply-linked file.
            # The no-follow open plus fstat check binds validation to the file
            # descriptor actually read, rather than a prior path lookup.
            info = self.path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeError("Encrypted memory file must not be a symbolic link.")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                    raise RuntimeError("Encrypted memory file must be a single-link regular file.")
                if opened.st_size > EncryptedMemoryStore.MAX_FILE_BYTES:
                    raise RuntimeError("Encrypted memory file exceeds the 10 MB safety limit.")
                chunks = []
                remaining = EncryptedMemoryStore.MAX_FILE_BYTES + 1
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                if len(raw) > EncryptedMemoryStore.MAX_FILE_BYTES:
                    raise RuntimeError("Encrypted memory file exceeds the 10 MB safety limit.")
            finally:
                os.close(descriptor)
            envelope = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Encrypted memory file is unreadable; no data was overwritten.") from exc
        if (
            not isinstance(envelope, dict)
            or envelope.get("version") != self.VERSION
            or not isinstance(envelope.get("nonce"), str)
            or not isinstance(envelope.get("ciphertext"), str)
        ):
            raise RuntimeError("Encrypted memory file has an invalid envelope.")
        try:
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            if len(nonce) != 12 or len(ciphertext) < 16:
                raise ValueError("invalid AES-GCM payload")
            plaintext = self._aesgcm(self._key).decrypt(nonce, ciphertext, _AAD)
            entries = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("Encrypted memory authentication failed; data was not changed.") from exc
        return self._validate_entries(entries)

    def _save(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        nonce = os.urandom(12)
        plaintext = json.dumps(
            entries[-self.MAX_ENTRIES:], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        ciphertext = self._aesgcm(self._key).encrypt(nonce, plaintext, _AAD)
        envelope = json.dumps({
            "version": self.VERSION,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }, separators=(",", ":"))
        if len(envelope.encode("utf-8")) > EncryptedMemoryStore.MAX_FILE_BYTES:
            raise RuntimeError("Encrypted memory file would exceed the 10 MB safety limit.")
        fd, temp_name = tempfile.mkstemp(prefix=".yuki-memory-", suffix=".tmp", dir=str(self.path.parent))
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(envelope)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
