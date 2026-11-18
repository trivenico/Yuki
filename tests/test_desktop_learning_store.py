import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.desktop.learning import (
    build_learning_center_callbacks,
    create_desktop_learning_coordinator,
    create_desktop_learning_session,
    create_desktop_learning_store,
)


class _Backend:
    priority = 1


class _Keyring:
    def __init__(self):
        self.values = {}

    def get_keyring(self):
        return _Backend()

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value


class DesktopLearningStoreTests(unittest.TestCase):
    def test_learning_center_callbacks_audit_approve_and_reject(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except ImportError:
            self.skipTest("cryptography optional dependency not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "yuki-profile"
            keyring = _Keyring()
            with patch("ui.desktop.learning.desktop_data_root", return_value=root):
                coordinator, ledger = create_desktop_learning_coordinator(
                    keyring, actor_id="authenticated-owner",
                )
                callbacks = build_learning_center_callbacks(coordinator, ledger)
                callbacks["on_consent"]("learning", True)
                callbacks["on_consent"]("training", True)
                first = coordinator.propose_correction(
                    messages=[
                        {"role": "user", "content": "Review this sample."},
                        {"role": "assistant", "content": "This is a suitable example."},
                    ],
                    source_reference="task:approve",
                    task_type="coding",
                    learning_consent=True,
                )
                review = callbacks["on_review"](first)
                self.assertEqual(review["proposal_id"], first)
                callbacks["on_approve"](first)
                second = coordinator.propose_correction(
                    messages=[
                        {"role": "user", "content": "Review another sample."},
                        {"role": "assistant", "content": "This sample should be discarded."},
                    ],
                    source_reference="task:reject",
                    task_type="coding",
                    learning_consent=True,
                )
                callbacks["on_reject"](second)
                third = coordinator.propose_correction(
                    messages=[
                        {"role": "user", "content": "Review a third sample."},
                        {"role": "assistant", "content": "Keep this pending until reviewed."},
                    ],
                    source_reference="task:pending-delete",
                    task_type="coding",
                    learning_consent=True,
                )
                state = callbacks["on_refresh"]()
                deleted = callbacks["on_delete_all"]()
                after_delete = callbacks["on_refresh"]()
            self.assertEqual(deleted, {"pending": 1, "approved": 1})
            self.assertEqual(after_delete["approved_count"], 0)
            self.assertEqual(after_delete["retained_pending_count"], 0)
            self.assertEqual(state["approved_count"], 1)
            self.assertEqual(state["pending"], [])
            self.assertTrue(ledger.verify_example_approval(first, "authenticated-owner"))
            self.assertFalse(ledger.verify_example_approval(second, "authenticated-owner"))
            self.assertFalse(ledger.verify_example_approval(first, "authenticated-owner"))
            self.assertFalse(ledger.verify_example_approval(third, "authenticated-owner"))

    def test_factory_builds_restart_safe_actor_bound_learning_coordinator(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except ImportError:
            self.skipTest("cryptography optional dependency not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "yuki-profile"
            keyring = _Keyring()
            with patch("ui.desktop.learning.desktop_data_root", return_value=root):
                coordinator, ledger = create_desktop_learning_coordinator(
                    keyring, actor_id="authenticated-owner",
                )
                ledger.record_consent("learning", True)
                ledger.record_consent("training", True)
                proposal = coordinator.propose_correction(
                    messages=[
                        {"role": "user", "content": "How should I test this?"},
                        {"role": "assistant", "content": "Use a focused unit test."},
                    ],
                    source_reference="task:desktop-learning",
                    task_type="coding",
                    learning_consent=True,
                )
                ledger.record_example_approval(proposal, True)
                example_id = coordinator.approve_correction(
                    proposal, training_consent=True, approved_by="authenticated-owner",
                )
                reopened, reopened_ledger = create_desktop_learning_coordinator(
                    keyring, actor_id="authenticated-owner",
                )
            self.assertEqual(reopened.approved_example_count, 1)
            self.assertTrue(reopened_ledger.has_consent("learning"))
            self.assertTrue(reopened_ledger.has_consent("training"))
            self.assertIsNotNone(reopened.build_few_shot_patch("Python unit test"))
            self.assertEqual(len(example_id), 24)

    def test_session_accepts_only_explicit_correction_payload_with_learning_consent(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except ImportError:
            self.skipTest("cryptography optional dependency not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "yuki-profile"
            with patch("ui.desktop.learning.desktop_data_root", return_value=root):
                session = create_desktop_learning_session(
                    _Keyring(), actor_id="authenticated-owner",
                )
                messages = [
                    {"role": "user", "content": "How should I test this?"},
                    {"role": "assistant", "content": "Use a focused unit test."},
                ]
                with self.assertRaises(Exception):
                    session.submit_correction(
                        messages=messages, source_reference="conversation:turn-1",
                        task_type="coding",
                    )
                session.callbacks["on_consent"]("learning", True)
                proposal = session.submit_correction(
                    messages=messages, source_reference="conversation:turn-1",
                    task_type="coding",
                )
                self.assertEqual(session.callbacks["on_review"](proposal)["messages"], messages)
                session.close()

    def test_desktop_learning_session_closes_coordinator_and_audit_callbacks(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except ImportError:
            self.skipTest("cryptography optional dependency not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "yuki-profile"
            with patch("ui.desktop.learning.desktop_data_root", return_value=root):
                session = create_desktop_learning_session(
                    _Keyring(), actor_id="authenticated-owner",
                )
                callbacks = session.callbacks
                callbacks["on_consent"]("learning", True)
                session.close()
                self.assertTrue(session.closed)
                self.assertIsNone(session.build_few_shot_patch("Python tests", max_examples=3, max_chars=4000))
                self.assertFalse(callbacks["on_refresh"]()["learning_consent"])
                with self.assertRaises(RuntimeError):
                    _ = session.callbacks
                with self.assertRaises(Exception):
                    callbacks["on_consent"]("training", True)

    def test_factory_uses_private_profile_scoped_encrypted_store(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except ImportError:
            self.skipTest("cryptography optional dependency not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "yuki-profile"
            keyring = _Keyring()
            with patch("ui.desktop.learning.desktop_data_root", return_value=root):
                store = create_desktop_learning_store(keyring)
                store.update(lambda state: state["pending"].clear())
                reopened = create_desktop_learning_store(keyring)
            self.assertEqual(reopened.read(), {"schema_version": 1, "pending": [], "approved": []})
            self.assertTrue((root / "learning.enc.json").is_file())
            self.assertTrue(any(service == "yuki-learning-store" for service, _ in keyring.values))
            self.assertFalse(any(service == "yuki-companion-memory" for service, _ in keyring.values))
            self.assertNotEqual(str(root / "learning.enc.json"), str(Path.cwd() / "learning.enc.json"))


if __name__ == "__main__":
    unittest.main()
