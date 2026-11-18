"""Encrypted, transaction-safe persistence for explicitly curated Yuki learning data.

The encryption key lives in the OS keyring. The file contains only an
AES-256-GCM authenticated envelope. Mutations are serialized across threads
and processes; a failed read or authentication check never overwrites data.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import re
import hashlib
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Callable, Iterator, TypeVar

_SERVICE = "yuki-learning-store"
_AAD_PREFIX = b"yuki-learning-store:v1:"
_MAX_FILE_BYTES = 20 * 1024 * 1024
_MAX_PENDING = 100
_MAX_APPROVED = 10_000
T = TypeVar("T")


class SecureLearningStoreError(RuntimeError):
    """Raised when encrypted learning state cannot be safely accessed."""


class EncryptedLearningStore:
    """Profile-scoped encrypted state with atomic cross-process transactions."""

    def __init__(
        self,
        path: str | Path,
        *,
        keyring_module=None,
        aesgcm_class=None,
        keyring_account: str,
    ) -> None:
        if not isinstance(keyring_account, str) or not keyring_account.strip() or len(keyring_account) > 128:
            raise ValueError("keyring_account must be bounded non-empty text")
        self.path = Path(path).expanduser()
        if self.path.is_symlink():
            raise SecureLearningStoreError("Learning data path must not be a symbolic link.")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.is_symlink() or not self.path.parent.is_dir():
            raise SecureLearningStoreError("Learning data directory is unavailable or unsafe.")
        if os.name == "posix":
            info = self.path.parent.stat()
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise SecureLearningStoreError("Learning data directory must be private (mode 0700).")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise SecureLearningStoreError("Learning data directory is not owned by this user.")
        try:
            if keyring_module is None:
                import keyring as keyring_module
            if aesgcm_class is None:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM as aesgcm_class
        except ImportError as exc:
            raise SecureLearningStoreError(
                "Encrypted learning dependencies are missing; install the desktop or production-storage extra."
            ) from exc
        try:
            backend = keyring_module.get_keyring()
            priority = getattr(backend, "priority", 0)
            if not isinstance(priority, (int, float)) or priority <= 0:
                raise ValueError("no secure OS keyring")
        except Exception as exc:
            raise SecureLearningStoreError("A secure operating-system keyring is required.") from exc
        self._keyring = keyring_module
        self._aesgcm = aesgcm_class
        self._account = keyring_account
        self._aad = _AAD_PREFIX + keyring_account.encode("utf-8")
        self._lock = RLock()
        self._key = self._load_or_create_key()

    def _load_or_create_key(self) -> bytes:
        # Serialize first-key creation across processes. Otherwise two fresh
        # instances could race and one could encrypt with a key overwritten by
        # the other process in the OS keyring.
        try:
            with self._lock, self._process_lock():
                encoded = self._keyring.get_password(_SERVICE, self._account)
                if encoded is None and self.path.exists():
                    raise ValueError("existing encrypted learning data has no key")
                if encoded is None:
                    key = os.urandom(32)
                    candidate = base64.b64encode(key).decode("ascii")
                    self._keyring.set_password(_SERVICE, self._account, candidate)
                    encoded = self._keyring.get_password(_SERVICE, self._account)
                    if encoded != candidate:
                        raise ValueError("keyring did not persist learning key")
                key = base64.b64decode(encoded, validate=True)
                if len(key) != 32:
                    raise ValueError("invalid AES-256 key")
                return key
        except Exception as exc:
            if isinstance(exc, SecureLearningStoreError):
                raise
            raise SecureLearningStoreError("Could not safely access the learning encryption key.") from exc

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        lock_path = self.path.with_name(self.path.name + ".lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SecureLearningStoreError("Learning lock file is not a regular private file.")
            if os.name == "posix":
                if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                    raise SecureLearningStoreError("Learning lock file has an unexpected owner.")
                os.fchmod(descriptor, 0o600)
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            elif os.name == "nt":
                import msvcrt
                if info.st_size == 0:
                    os.write(descriptor, b"\0")
                    os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                raise SecureLearningStoreError("This platform has no supported process-lock implementation.")
            yield
        finally:
            try:
                if os.name == "posix":
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                elif os.name == "nt":
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            os.close(descriptor)

    @staticmethod
    def _empty_state() -> dict:
        return {"schema_version": 1, "pending": [], "approved": []}

    @staticmethod
    def _validate_state(state: object) -> dict:
        from training.sft_data_generator import _canonical_messages
        if not isinstance(state, dict) or set(state) != {"schema_version", "pending", "approved"}:
            raise SecureLearningStoreError("Learning state has an invalid structure.")
        if state.get("schema_version") != 1:
            raise SecureLearningStoreError("Learning state schema version is unsupported.")
        pending, approved = state.get("pending"), state.get("approved")
        if not isinstance(pending, list) or len(pending) > _MAX_PENDING:
            raise SecureLearningStoreError("Pending learning queue is invalid or exceeds its limit.")
        if not isinstance(approved, list) or len(approved) > _MAX_APPROVED:
            raise SecureLearningStoreError("Approved learning dataset is invalid or exceeds its limit.")
        seen_pending, seen_approved = set(), set()
        for item in pending:
            if not isinstance(item, dict) or set(item) != {
                "proposal_id", "messages", "source_reference", "task_type", "created_at",
            }:
                raise SecureLearningStoreError("Pending correction record is malformed.")
            proposal_id = item["proposal_id"]
            if not isinstance(proposal_id, str) or len(proposal_id) != 24 or any(c not in "0123456789abcdef" for c in proposal_id) or proposal_id in seen_pending:
                raise SecureLearningStoreError("Pending correction identifier is invalid or duplicated.")
            seen_pending.add(proposal_id)
            normalized = _canonical_messages(item["messages"])
            if not isinstance(item["source_reference"], str) or not item["source_reference"].strip() or len(item["source_reference"]) > 512:
                raise SecureLearningStoreError("Pending correction source reference is invalid.")
            if not isinstance(item["task_type"], str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", item["task_type"]):
                raise SecureLearningStoreError("Pending correction task type is invalid.")
            expected = hashlib.sha256(
                (item["source_reference"] + "\0" + item["task_type"] + "\0" +
                 "\0".join(message["role"] + "\0" + message["content"] for message in normalized)).encode("utf-8")
            ).hexdigest()[:24]
            if proposal_id != expected:
                raise SecureLearningStoreError("Pending correction digest does not match its contents.")
            if not isinstance(item["created_at"], str) or len(item["created_at"]) > 64:
                raise SecureLearningStoreError("Pending correction timestamp is invalid.")
        for item in approved:
            if not isinstance(item, dict) or set(item) != {
                "example_id", "proposal_id", "messages", "source_reference",
                "task_type", "approved_by", "approved_at",
            }:
                raise SecureLearningStoreError("Approved learning record is malformed.")
            example_id, proposal_id = item["example_id"], item["proposal_id"]
            for value in (example_id, proposal_id):
                if not isinstance(value, str) or len(value) != 24 or any(c not in "0123456789abcdef" for c in value):
                    raise SecureLearningStoreError("Approved learning identifier is invalid.")
            if example_id in seen_approved:
                raise SecureLearningStoreError("Approved learning example is duplicated.")
            seen_approved.add(example_id)
            normalized = _canonical_messages(item["messages"])
            if not isinstance(item["source_reference"], str) or not item["source_reference"].strip() or len(item["source_reference"]) > 512:
                raise SecureLearningStoreError("Approved learning source reference is invalid.")
            if not isinstance(item["task_type"], str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", item["task_type"]):
                raise SecureLearningStoreError("Approved learning task type is invalid.")
            canonical = json.dumps(
                {"task_type": item["task_type"], "messages": normalized},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            if example_id != hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]:
                raise SecureLearningStoreError("Approved example digest does not match its contents.")
            expected_proposal = hashlib.sha256(
                (item["source_reference"] + "\0" + item["task_type"] + "\0" +
                 "\0".join(message["role"] + "\0" + message["content"] for message in normalized)).encode("utf-8")
            ).hexdigest()[:24]
            if proposal_id != expected_proposal:
                raise SecureLearningStoreError("Approved proposal digest does not match its source contents.")
            if not isinstance(item["approved_by"], str) or not item["approved_by"].strip() or len(item["approved_by"]) > 160:
                raise SecureLearningStoreError("Approved learning reviewer is invalid.")
            if not isinstance(item["approved_at"], str) or len(item["approved_at"]) > 64:
                raise SecureLearningStoreError("Approved learning timestamp is invalid.")
        return copy.deepcopy(state)

    def _read_unlocked(self) -> dict:
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return self._empty_state()
        except OSError as exc:
            raise SecureLearningStoreError("Encrypted learning file cannot be opened safely.") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size <= 0 or info.st_size > _MAX_FILE_BYTES:
                raise SecureLearningStoreError("Encrypted learning file is not a bounded regular file.")
            chunks, remaining = [], _MAX_FILE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > _MAX_FILE_BYTES:
                raise SecureLearningStoreError("Encrypted learning file exceeds its size limit.")
        finally:
            os.close(descriptor)
        try:
            envelope = json.loads(raw.decode("utf-8"))
            if not isinstance(envelope, dict) or set(envelope) != {"version", "nonce", "ciphertext"} or envelope["version"] != 1:
                raise ValueError("invalid envelope")
            nonce = base64.b64decode(envelope["nonce"], validate=True)
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            if len(nonce) != 12 or len(ciphertext) < 16:
                raise ValueError("invalid encrypted payload")
            plaintext = self._aesgcm(self._key).decrypt(nonce, ciphertext, self._aad)
            return self._validate_state(json.loads(plaintext.decode("utf-8")))
        except Exception as exc:
            if isinstance(exc, SecureLearningStoreError):
                raise
            raise SecureLearningStoreError("Encrypted learning authentication or validation failed; data was not changed.") from exc

    def _write_unlocked(self, state: dict) -> None:
        validated = self._validate_state(state)
        nonce = os.urandom(12)
        plaintext = json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ciphertext = self._aesgcm(self._key).encrypt(nonce, plaintext, self._aad)
        envelope = json.dumps({
            "version": 1,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }, separators=(",", ":")).encode("utf-8")
        if len(envelope) > _MAX_FILE_BYTES:
            raise SecureLearningStoreError("Encrypted learning state would exceed its size limit.")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".yuki-learning-", suffix=".tmp", dir=str(self.path.parent))
        try:
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(envelope)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
            if os.name == "posix":
                directory_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def read(self) -> dict:
        with self._lock, self._process_lock():
            return self._read_unlocked()

    def update(self, mutator: Callable[[dict], T]) -> T:
        """Run one atomic read/modify/write transaction under cross-process lock."""
        if not callable(mutator):
            raise TypeError("mutator must be callable")
        with self._lock, self._process_lock():
            state = self._read_unlocked()
            result = mutator(state)
            self._write_unlocked(state)
            return result

    def replace(self, state: dict) -> None:
        """Atomically replace all learning state; caller must obtain user approval."""
        with self._lock, self._process_lock():
            self._write_unlocked(state)
