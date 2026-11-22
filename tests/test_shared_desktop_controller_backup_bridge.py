import unittest
from pathlib import Path

from ui.desktop.shared_desktop_controller import SharedDesktopControllerBridge


class FakeChat:
    def respond(self, message, **kwargs):
        return message

    def list_conversations(self, *, limit=50):
        return []

    def select_conversation(self, conversation_id):
        return conversation_id

    def history(self, *, limit=100):
        return []

    def set_memory_context(self, enabled):
        self.context_enabled = enabled

    def memory_context_enabled(self):
        return getattr(self, "context_enabled", False)

    def logout(self):
        return None


class RecordingSpecialistController:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "logout":
                return True
            return {"method": name}

        return record


class SharedDesktopControllerBackupBridgeTests(unittest.TestCase):
    def setUp(self):
        self.specialists = RecordingSpecialistController()
        self.bridge = SharedDesktopControllerBridge(FakeChat(), self.specialists)

    def test_create_backup_forwards_explicit_memory_key_export_opt_in(self):
        destination = Path("portable.yukibak")

        result = self.bridge.create_backup(
            destination,
            "a sufficiently long passphrase",
            include_memory_key=True,
        )

        self.assertEqual(result, {"method": "create_backup"})
        self.assertEqual(
            self.specialists.calls,
            [(
                "create_backup",
                (destination, "a sufficiently long passphrase"),
                {"include_memory_key": True},
            )],
        )

    def test_create_backup_defaults_memory_key_export_to_disabled(self):
        destination = Path("portable.yukibak")

        self.bridge.create_backup(destination, "a sufficiently long passphrase")

        self.assertEqual(
            self.specialists.calls[0][2],
            {"include_memory_key": False},
        )

    def test_restore_backup_forwards_explicit_keyring_import_opt_in(self):
        backup = Path("portable.yukibak")
        destination = Path("restored-profile")

        result = self.bridge.restore_backup(
            backup,
            destination,
            "a sufficiently long passphrase",
            import_memory_key=True,
        )

        self.assertEqual(result, {"method": "restore_backup"})
        self.assertEqual(
            self.specialists.calls,
            [(
                "restore_backup",
                (backup, destination, "a sufficiently long passphrase"),
                {"import_memory_key": True},
            )],
        )

    def test_restore_backup_defaults_keyring_import_to_disabled(self):
        self.bridge.restore_backup(
            Path("portable.yukibak"),
            Path("restored-profile"),
            "a sufficiently long passphrase",
        )

        self.assertEqual(
            self.specialists.calls[0][2],
            {"import_memory_key": False},
        )


if __name__ == "__main__":
    unittest.main()
