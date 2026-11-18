import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui.desktop.learning import create_desktop_learning_store


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
