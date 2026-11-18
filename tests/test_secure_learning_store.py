import base64
import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path

from training.secure_learning_store import EncryptedLearningStore, SecureLearningStoreError


class _Backend:
    priority = 1


class _SharedKeyring:
    def __init__(self, values):
        self.values = values

    def get_keyring(self):
        return _Backend()

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value


def _process_append(path, shared_values, index):
    store = EncryptedLearningStore(
        path, keyring_module=_SharedKeyring(shared_values),
        keyring_account="profile:concurrency",
    )
    user_text = f"Question {index}"
    answer_text = f"Answer {index}"
    import hashlib
    source = f"task:{index}"
    canonical = source + "\0" + "coding" + "\0" + "user\0" + user_text + "\0" + "assistant\0" + answer_text
    proposal_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    record = {
        "proposal_id": proposal_id,
        "messages": [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": answer_text},
        ],
        "source_reference": source,
        "task_type": "coding",
        "created_at": "2026-11-18T00:00:00+00:00",
    }
    store.update(lambda state: state["pending"].append(record))


class EncryptedLearningStoreTests(unittest.TestCase):
    def setUp(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            self.skipTest("cryptography optional dependency not installed")
        self.aesgcm = AESGCM
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.values = {}
        self.keyring = _SharedKeyring(self.values)
        self.path = self.root / "learning.enc.json"

    def tearDown(self):
        self.temp.cleanup()

    def make_store(self, path=None, account="profile:test"):
        return EncryptedLearningStore(
            path or self.path, keyring_module=self.keyring,
            aesgcm_class=self.aesgcm, keyring_account=account,
        )

    def test_state_is_encrypted_and_survives_reopen(self):
        store = self.make_store()
        store.replace({"schema_version": 1, "pending": [], "approved": []})
        record = {"marker": "not allowed"}  # Invalid records cannot be persisted.
        with self.assertRaises(SecureLearningStoreError):
            store.update(lambda state: state["pending"].append(record))
        state = store.read()
        self.assertEqual(state, {"schema_version": 1, "pending": [], "approved": []})
        raw = self.path.read_bytes()
        self.assertNotIn(b"not allowed", raw)
        reopened = self.make_store()
        self.assertEqual(reopened.read(), state)
        self.assertEqual(json.loads(raw)["version"], 1)

    def test_valid_pending_record_round_trips(self):
        store = self.make_store()
        import hashlib
        messages = [
            {"role": "user", "content": "How do I test this?"},
            {"role": "assistant", "content": "Use a focused unit test."},
        ]
        source = "task:unit-test"
        canonical = source + "\0" + "coding" + "\0" + "user\0" + messages[0]["content"] + "\0" + "assistant\0" + messages[1]["content"]
        proposal_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        row = {
            "proposal_id": proposal_id, "messages": messages,
            "source_reference": source, "task_type": "coding",
            "created_at": "2026-11-18T00:00:00+00:00",
        }
        store.update(lambda state: state["pending"].append(row))
        self.assertEqual(store.read()["pending"], [row])
        self.assertNotIn(messages[0]["content"].encode(), self.path.read_bytes())

    def test_authentication_failure_preserves_original_ciphertext(self):
        store = self.make_store()
        store.replace({"schema_version": 1, "pending": [], "approved": []})
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        envelope["ciphertext"] = base64.b64encode(b"x" * 64).decode("ascii")
        self.path.write_text(json.dumps(envelope), encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(SecureLearningStoreError, "authentication"):
            store.read()
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_key_does_not_replace_existing_ciphertext(self):
        store = self.make_store()
        store.replace({"schema_version": 1, "pending": [], "approved": []})
        before = self.path.read_bytes()
        self.values.clear()
        with self.assertRaisesRegex(SecureLearningStoreError, "encryption key"):
            self.make_store()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.values, {})

    def test_profile_key_isolation(self):
        first = self.make_store(account="profile:first")
        first.replace({"schema_version": 1, "pending": [], "approved": []})
        # Provision a different valid key for the second profile on its own file.
        self.make_store(
            path=self.root / "other.enc.json", account="profile:second",
        ).replace({"schema_version": 1, "pending": [], "approved": []})
        with self.assertRaisesRegex(SecureLearningStoreError, "authentication"):
            self.make_store(account="profile:second").read()

    def test_mutation_is_serialized_across_processes(self):
        try:
            import multiprocessing
            context = multiprocessing.get_context("spawn")
            with context.Manager() as manager:
                shared = manager.dict()
                # Seed key and file before workers start.
                seeded = EncryptedLearningStore(
                    self.path, keyring_module=_SharedKeyring(shared),
                    aesgcm_class=self.aesgcm, keyring_account="profile:concurrency",
                )
                seeded.read()
                processes = [
                    context.Process(target=_process_append, args=(str(self.path), shared, index))
                    for index in range(4)
                ]
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(30)
                self.assertTrue(all(not process.is_alive() for process in processes))
                self.assertEqual([process.exitcode for process in processes], [0] * 4)
                result = EncryptedLearningStore(
                    self.path, keyring_module=_SharedKeyring(shared),
                    aesgcm_class=self.aesgcm, keyring_account="profile:concurrency",
                ).read()
                self.assertEqual(len(result["pending"]), 4)
        except (OSError, RuntimeError) as exc:
            self.skipTest(f"multiprocessing unavailable in this environment: {exc}")

    def test_approved_record_integrity_is_checked_before_persistence(self):
        from training.sft_data_generator import SFTDataGenerator
        messages = [
            {"role": "user", "content": "How should I test this?"},
            {"role": "assistant", "content": "Use a focused unit test."},
        ]
        source = "task:approved"
        generator = SFTDataGenerator()
        example_id = generator.add_example(
            messages=messages, source_reference=source, task_type="coding",
            training_consent=True, human_approved=True, approved_by="owner",
        )
        import hashlib
        canonical_source = source + "\0" + "coding" + "\0" + "user\0" + messages[0]["content"] + "\0" + "assistant\0" + messages[1]["content"]
        proposal_id = hashlib.sha256(canonical_source.encode("utf-8")).hexdigest()[:24]
        row = {
            "example_id": example_id, "proposal_id": proposal_id,
            "messages": messages, "source_reference": source, "task_type": "coding",
            "approved_by": "owner", "approved_at": "2026-11-18T00:00:00+00:00",
        }
        store = self.make_store()
        store.replace({"schema_version": 1, "pending": [], "approved": [row]})
        self.assertEqual(store.read()["approved"], [row])
        altered = dict(row)
        altered["messages"] = [
            {"role": "user", "content": "Altered question"},
            {"role": "assistant", "content": "Use a focused unit test."},
        ]
        with self.assertRaisesRegex(SecureLearningStoreError, "digest"):
            store.replace({"schema_version": 1, "pending": [], "approved": [altered]})

    def test_hard_linked_encrypted_file_is_rejected(self):
        if os.name != "posix":
            self.skipTest("POSIX hard-link contract")
        store = self.make_store()
        store.replace({"schema_version": 1, "pending": [], "approved": []})
        alias = self.root / "learning-alias.enc.json"
        os.link(self.path, alias)
        with self.assertRaisesRegex(SecureLearningStoreError, "regular file"):
            store.read()

    def test_symbolic_link_data_path_is_rejected(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links unavailable")
        target = self.root / "target.enc.json"
        target.write_text("{}", encoding="utf-8")
        alias = self.root / "alias.enc.json"
        try:
            alias.symlink_to(target)
        except OSError:
            self.skipTest("symbolic link creation is not permitted")
        with self.assertRaisesRegex(SecureLearningStoreError, "symbolic link"):
            EncryptedLearningStore(
                alias, keyring_module=self.keyring,
                aesgcm_class=self.aesgcm, keyring_account="profile:alias",
            )

    def test_world_readable_encrypted_file_is_rejected(self):
        if os.name != "posix":
            self.skipTest("POSIX permission contract")
        store = self.make_store()
        store.replace({"schema_version": 1, "pending": [], "approved": []})
        self.path.chmod(0o644)
        with self.assertRaisesRegex(SecureLearningStoreError, "permissions"):
            store.read()

    def test_known_plaintext_keyring_backend_is_rejected(self):
        class PlaintextKeyring:
            priority = 1

        class PlaintextModule(_SharedKeyring):
            def get_keyring(self):
                return PlaintextKeyring()

        with self.assertRaisesRegex(SecureLearningStoreError, "keyring"):
            EncryptedLearningStore(
                self.path, keyring_module=PlaintextModule(self.values),
                aesgcm_class=self.aesgcm, keyring_account="profile:plaintext",
            )
        self.assertEqual(self.values, {})

    def test_private_parent_directory_is_required(self):
        if os.name != "posix":
            self.skipTest("POSIX permission contract")
        public = self.root / "public"
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        with self.assertRaisesRegex(SecureLearningStoreError, "private"):
            EncryptedLearningStore(
                public / "learning.enc.json", keyring_module=self.keyring,
                aesgcm_class=self.aesgcm, keyring_account="profile:public",
            )


if __name__ == "__main__":
    unittest.main()
