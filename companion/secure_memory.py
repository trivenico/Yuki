"""Optional AES-256-GCM encrypted local companion memory.

The encryption key is stored in the operating-system keyring, never beside the
ciphertext. This backend deliberately fails closed when a secure keyring is not
available; it never silently falls back to plaintext.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

from companion.memory import MemoryStore

_SERVICE = "yuki-companion-memory"
_ACCOUNT = "default"
_AAD = b"yuki-companion-memory:v1"


class EncryptedMemoryStore(MemoryStore):
    VERSION = 1

    def __init__(self, path: str | Path = "yuki_memory.enc.json", *, keyring_module=None, aesgcm_class=None):
        super().__init__(path)
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
            encoded = self._keyring.get_password(_SERVICE, _ACCOUNT)
            if encoded is None:
                key = os.urandom(32)
                self._keyring.set_password(_SERVICE, _ACCOUNT, base64.b64encode(key).decode("ascii"))
                encoded = self._keyring.get_password(_SERVICE, _ACCOUNT)
            key = base64.b64decode(encoded, validate=True)
            if len(key) != 32:
                raise ValueError("invalid key length")
            return key
        except Exception as exc:
            raise RuntimeError("Could not safely access the Yuki memory encryption key.") from exc

    def _load(self) -> list[dict]:
        try:
            if self.path.stat().st_size > self.MAX_FILE_BYTES:
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
        if len(envelope.encode("utf-8")) > self.MAX_FILE_BYTES:
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
