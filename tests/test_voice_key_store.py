import base64
import tempfile
import threading
import time
import unittest
from pathlib import Path

from voice.key_store import (
    UnsupportedCredentialBackendError,
    VoiceEncryptionKeyProvider,
    VoiceKeyStoreError,
)


class _NativeBackend:
    priority = 1


_NativeBackend.__module__ = "keyring.backends.SecretService"


class _PlaintextBackend:
    priority = 1


_PlaintextBackend.__module__ = "keyrings.alt.file"


class _FakeKeyring:
    def __init__(self, backend=None):
        self.backend = backend or _NativeBackend()
        self.values = {}
        self._lock = threading.Lock()

    def get_keyring(self):
        return self.backend

    def get_password(self, service, account):
        with self._lock:
            value = self.values.get((service, account))
        if value is None:
            time.sleep(0.03)
        return value

    def set_password(self, service, account, value):
        with self._lock:
            self.values[(service, account)] = value

    def delete_password(self, service, account):
        with self._lock:
            del self.values[(service, account)]


class _FileKeyring:
    """Shared-file fake backend for the cross-process regression test."""
    def __init__(self, path):
        self.path = str(path)
    def get_keyring(self):
        return _NativeBackend()
    def get_password(self, service, account):
        import json
        path = Path(self.path)
        if not path.exists():
            time.sleep(0.15)
            return None
        return json.loads(path.read_text(encoding="utf-8")).get(service + ":" + account)
    def set_password(self, service, account, value):
        import json
        path = Path(self.path)
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        state[service + ":" + account] = value
        time.sleep(0.05)
        path.write_text(json.dumps(state), encoding="utf-8")
    def delete_password(self, service, account):
        import json
        path = Path(self.path)
        state = json.loads(path.read_text(encoding="utf-8"))
        del state[service + ":" + account]
        path.write_text(json.dumps(state), encoding="utf-8")

def _create_key_in_process(lock_directory, backend_path, result_queue):
    try:
        provider = VoiceEncryptionKeyProvider("multi-process-user", lock_directory=lock_directory, keyring_module=_FileKeyring(backend_path))
        result_queue.put(("ok", provider.get_or_create_key().hex()))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__))

class VoiceEncryptionKeyProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lock_directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def provider(self, scope="user", backend=None):
        return VoiceEncryptionKeyProvider(
            scope, lock_directory=self.lock_directory, keyring_module=backend or _FakeKeyring(),
        )

    def test_creates_and_reuses_user_scoped_32_byte_key(self):
        backend = _FakeKeyring()
        first = self.provider("user-123", backend)
        second = self.provider("user-123", backend)
        key = first.get_or_create_key()
        self.assertEqual(len(key), 32)
        self.assertEqual(second.get_or_create_key(), key)
        self.assertEqual(len(backend.values), 1)

    def test_two_provider_instances_share_one_key_during_concurrent_creation(self):
        backend = _FakeKeyring()
        first = self.provider("concurrent-user", backend)
        second = self.provider("concurrent-user", backend)
        barrier = threading.Barrier(3)
        results = []
        errors = []

        def create(provider):
            try:
                barrier.wait()
                results.append(provider.get_or_create_key())
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=create, args=(first,)),
            threading.Thread(target=create, args=(second,)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(backend.values), 1)

    def test_separate_processes_do_not_split_first_created_key(self):
        import multiprocessing
        context = multiprocessing.get_context("spawn")
        backend_path = self.lock_directory / "shared-keyring.json"
        result_queue = context.Queue()
        processes = [
            context.Process(target=_create_key_in_process, args=(str(self.lock_directory), str(backend_path), result_queue))
            for _ in range(2)
        ]
        try:
            for process in processes:
                process.start()
            results = [result_queue.get(timeout=30) for _ in processes]
            for process in processes:
                process.join(timeout=10)
            self.assertTrue(all(process.exitcode == 0 for process in processes))
            self.assertTrue(all(status == "ok" for status, _ in results), results)
            self.assertEqual(results[0][1], results[1][1])
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            result_queue.close()

    def test_scopes_keys_per_user_without_storing_raw_identity(self):
        backend = _FakeKeyring()
        first = self.provider("user-one", backend)
        second = self.provider("user-two", backend)
        self.assertNotEqual(first.get_or_create_key(), second.get_or_create_key())
        account = next(iter(backend.values))[1]
        self.assertNotIn("user-one", account)
        self.assertNotIn("user-two", account)

    def test_rejects_plaintext_or_unrecognized_backend(self):
        provider = self.provider("user", _FakeKeyring(_PlaintextBackend()))
        with self.assertRaises(UnsupportedCredentialBackendError):
            provider.get_or_create_key()

    def test_rejects_invalid_stored_key(self):
        import hashlib
        backend = _FakeKeyring()
        backend.values[("Yuki.VoiceArtifacts.v1", hashlib.sha256(b"user").hexdigest())] = "bad"
        provider = self.provider("user", backend)
        with self.assertRaises(VoiceKeyStoreError):
            provider.get_or_create_key()

    def test_key_deletion_requires_confirmation_and_is_verified(self):
        backend = _FakeKeyring()
        provider = self.provider("user", backend)
        provider.get_or_create_key()
        with self.assertRaises(VoiceKeyStoreError):
            provider.delete_key(confirmed=False)
        provider.delete_key(confirmed=True)
        self.assertEqual(backend.values, {})

    def test_requires_non_empty_user_scope_and_lock_directory(self):
        with self.assertRaises(ValueError):
            VoiceEncryptionKeyProvider(" ", lock_directory=self.lock_directory, keyring_module=_FakeKeyring())
        with self.assertRaises(ValueError):
            VoiceEncryptionKeyProvider("user", lock_directory="", keyring_module=_FakeKeyring())

    def test_rejects_group_or_world_accessible_lock_directory(self):
        import os
        if os.name == "nt":
            self.skipTest("POSIX directory mode checks are not applicable on Windows.")
        insecure = self.lock_directory / "insecure"
        insecure.mkdir(mode=0o755)
        os.chmod(insecure, 0o755)
        provider = VoiceEncryptionKeyProvider(
            "user", lock_directory=insecure, keyring_module=_FakeKeyring(),
        )
        with self.assertRaises(VoiceKeyStoreError):
            provider.get_or_create_key()


if __name__ == "__main__":
    unittest.main()
