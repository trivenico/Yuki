import tempfile
import unittest
from pathlib import Path

from core.config import load_config
from core.conversation_store_factory import create_conversation_store
from core.conversation_store import ConversationStore


class ConversationStoreFactoryTests(unittest.TestCase):
    def test_sqlite_is_default_and_factory_creates_private_local_store(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            workspace = Path(directory) / "workspace"
            config_path.write_text(f"workspace: {workspace}\n", encoding="utf-8")
            config = load_config(config_path)
            self.assertEqual(config["workspace"], str(workspace.resolve()))
            store = create_conversation_store(config)
            self.assertIsInstance(store, ConversationStore)
            conversation = store.create_conversation(owner_id="factory-user")
            self.assertEqual(conversation["owner_id"], "factory-user")
            self.assertTrue(store.health_check())
            self.assertEqual(Path(store.database_path).stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
