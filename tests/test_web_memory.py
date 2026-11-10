import tempfile
import unittest
from pathlib import Path

from core.auth import BuiltInAuthService
from core.web_memory import build_web_reasoning_context


class _LocalEmbedder:
    is_local = True

    def __init__(self, model_path, *, device):
        self.model_path = model_path
        self.device = device

    def encode(self, text):
        return [1.0, 0.0]


class WebMemoryBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name) / "workspace"
        self.auth = BuiltInAuthService(str(Path(self.tempdir.name) / "auth.sqlite3"))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_memory_is_disabled_by_default_without_loading_embeddings(self):
        builder = build_web_reasoning_context(
            self.auth, self.workspace, environ={},
            embedder_factory=lambda *args, **kwargs: self.fail("must not load model"),
        )
        self.assertIsNone(builder)
        self.assertFalse(self.workspace.exists())

    def test_enabled_memory_requires_explicit_local_model_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "YUKI_EMBEDDING_MODEL_PATH"):
            build_web_reasoning_context(
                self.auth, self.workspace,
                environ={"YUKI_MEMORY_ENABLED": "true"},
            )

    def test_enabled_memory_builds_owner_authorized_persistent_local_stack(self):
        model_dir = Path(self.tempdir.name) / "reviewed-model"
        model_dir.mkdir()
        builder = build_web_reasoning_context(
            self.auth, self.workspace,
            environ={
                "YUKI_MEMORY_ENABLED": "true",
                "YUKI_EMBEDDING_MODEL_PATH": str(model_dir),
                "YUKI_EMBEDDING_MODEL_ID": "local-test-model@v1",
                "YUKI_EMBEDDING_DEVICE": "cpu",
            },
            embedder_factory=_LocalEmbedder,
        )
        self.assertIsNotNone(builder)
        store = builder.knowledge.retriever.memory_store
        owner = self.auth.create_account("memory.owner", "A sufficiently long password 3!")
        other = self.auth.create_account("memory.other", "Another secure password 4!")
        saved = store.remember(
            actor_id=owner.user_id, agent_id="yuki",
            content="The owner prefers concise responses.",
            source="owner supplied", category="semantic",
        )
        self.assertEqual(saved["actor_id"], owner.user_id)
        self.assertEqual(
            store.list_memories(actor_id=other.user_id, agent_id="yuki"), []
        )
        self.assertTrue((self.workspace / "agent-memory.sqlite3").is_file())
        self.assertEqual(
            builder.knowledge.retriever.embedding_index.model_id,
            "local-test-model@v1",
        )

    def test_invalid_feature_flag_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "must be true or false"):
            build_web_reasoning_context(
                self.auth, self.workspace, environ={"YUKI_MEMORY_ENABLED": "sometimes"},
            )


if __name__ == "__main__":
    unittest.main()
