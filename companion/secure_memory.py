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
import tempfile
from pathlib import Path

from companion.memory import MemoryStore

_SERVICE = "yuki-companion-memory"
_ACCOUNT = "default"
_AAD = b"yuki-companion-memory:v1"


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
            encoded = self._keyring.get_password(_SERVICE, self._keyring_account)
            if encoded is None and self._keyring_account != _ACCOUNT and self.path.exists():
                # One-time, lossless migration from the original shared keyring
                # account. Never generate a replacement key for existing ciphertext.
                legacy = self._keyring.get_password(_SERVICE, _ACCOUNT)
                if legacy is None:
                    raise RuntimeError("An existing encrypted memory file has no keyring key.")
                self._keyring.set_password(_SERVICE, self._keyring_account, legacy)
                encoded = self._keyring.get_password(_SERVICE, self._keyring_account)
                if encoded != legacy:
                    raise RuntimeError("Could not verify migrated memory key.")
            if encoded is None and self.path.exists():
                raise RuntimeError("An existing encrypted memory file has no keyring key.")
            if encoded is None:
                key = os.urandom(32)
                self._keyring.set_password(_SERVICE, self._keyring_account, base64.b64encode(key).decode("ascii"))
                encoded = self._keyring.get_password(_SERVICE, self._keyring_account)
            key = base64.b64decode(encoded, validate=True)
            if len(key) != 32:
                raise ValueError("invalid key length")
            return key
        except Exception as exc:
            raise RuntimeError("Could not safely access the Yuki memory encryption key.") from exc

    def export_key_for_encrypted_backup(self, profile_id: str) -> bytes:
        """Return authenticated key metadata for inclusion only inside an encrypted archive."""
        from core.profile_identity import profile_memory_keyring_account
        if self._keyring_account != profile_memory_keyring_account(profile_id):
            raise RuntimeError("Memory key does not belong to the requested profile.")
        payload = {
            "schema_version": 1,
            "profile_id": profile_id,
            "key": base64.b64encode(self._key).decode("ascii"),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def import_key_from_encrypted_backup(
        payload: bytes, profile_id: str, *, keyring_module, memory_path: str | Path,
    ) -> bool:
        """Install a verified backup DEK in the OS keyring; return whether a key was created."""
        from core.profile_identity import profile_memory_keyring_account
        if not isinstance(payload, bytes) or len(payload) > 2048:
            raise RuntimeError("Portable memory key metadata is invalid.")
        try:
            metadata = json.loads(payload.decode("utf-8"))
            if (
                not isinstance(metadata, dict)
                or set(metadata) != {"schema_version", "profile_id", "key"}
                or metadata.get("schema_version") != 1
                or metadata.get("profile_id") != profile_id
                or not isinstance(metadata.get("key"), str)
            ):
                raise ValueError("invalid portable key metadata")
            key = base64.b64decode(metadata["key"], validate=True)
            if len(key) != 32:
                raise ValueError("invalid portable memory key length")
            memory_file = Path(memory_path).expanduser()
            info = memory_file.lstat()
            if (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size <= 0 or info.st_size > EncryptedMemoryStore.MAX_FILE_BYTES
            ):
                raise ValueError("invalid encrypted memory file")
            envelope = json.loads(memory_file.read_text(encoding="utf-8"))
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
            account = profile_memory_keyring_account(profile_id)
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
            if self.path.stat().st_size > EncryptedMemoryStore.MAX_FILE_BYTES:
                raise RuntimeError("Encrypted memory file exceeds the 10 MB safety limit.")
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
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
