import sys, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch
from providers.local_embeddings import HuggingFaceLocalEmbedder, LocalHashingEmbedder


class FakeSentenceTransformer:
    instances = []
    def __init__(self, path, **kwargs):
        self.path, self.kwargs, self.calls = path, kwargs, []
        self.instances.append(self)
    def encode(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        return [[1.0, 0.0] if i == 0 else [0.0, 1.0] for i, _ in enumerate(texts)]


class LocalEmbeddingAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        FakeSentenceTransformer.instances.clear()
        module = types.ModuleType("sentence_transformers")
        module.SentenceTransformer = FakeSentenceTransformer
        patcher = patch.dict(sys.modules, {"sentence_transformers": module})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_load_is_local_only_and_disables_remote_code(self):
        embedder = HuggingFaceLocalEmbedder(self.tmp.name)
        model = FakeSentenceTransformer.instances[-1]
        self.assertEqual(model.path, str(Path(self.tmp.name).resolve()))
        self.assertFalse(model.kwargs["trust_remote_code"])
        self.assertTrue(model.kwargs["model_kwargs"]["local_files_only"])
        self.assertTrue(model.kwargs["tokenizer_kwargs"]["local_files_only"])
        self.assertTrue(embedder.is_local)

    def test_batch_encoding_uses_single_model_call(self):
        embedder = HuggingFaceLocalEmbedder(self.tmp.name, batch_size=8)
        self.assertEqual(embedder.encode_many(["first", "second"]), [[1.0, 0.0], [0.0, 1.0]])
        model = FakeSentenceTransformer.instances[-1]
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.calls[0][1]["batch_size"], 8)

    def test_single_encode_delegates_to_batch(self):
        self.assertEqual(HuggingFaceLocalEmbedder(self.tmp.name).encode("hello"), [1.0, 0.0])

    def test_missing_directory_rejected(self):
        with self.assertRaisesRegex(ValueError, "not_found"):
            HuggingFaceLocalEmbedder(str(Path(self.tmp.name) / "missing"))

    def test_invalid_device_and_batch_limits_rejected(self):
        with self.assertRaisesRegex(ValueError, "device_must"):
            HuggingFaceLocalEmbedder(self.tmp.name, device="remote")
        with self.assertRaisesRegex(ValueError, "batch_size"):
            HuggingFaceLocalEmbedder(self.tmp.name, batch_size=0)

    def test_text_limits_rejected(self):
        embedder = HuggingFaceLocalEmbedder(self.tmp.name, max_text_chars=5)
        with self.assertRaisesRegex(ValueError, "too_long"):
            embedder.encode("too long text")
        with self.assertRaisesRegex(ValueError, "batch_must"):
            embedder.encode_many([])

    def test_non_finite_vectors_rejected(self):
        embedder = HuggingFaceLocalEmbedder(self.tmp.name)
        FakeSentenceTransformer.instances[-1].encode = lambda *_a, **_k: [[float("nan"), 0.0]]
        with self.assertRaisesRegex(ValueError, "non_finite"):
            embedder.encode("test")



    def test_hashing_fallback_is_deterministic_and_offline(self):
        embedder = LocalHashingEmbedder(dimensions=128)
        first = embedder.encode("local private memory retrieval")
        second = embedder.encode("local private memory retrieval")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 128)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0, places=6)
        self.assertEqual(embedder.encode_many(["alpha", "beta"])[0], embedder.encode("alpha"))
        self.assertEqual(embedder.model_id, "yuki-local-hashing-v1")

    def test_hashing_fallback_rejects_invalid_dimensions_and_empty_text(self):
        with self.assertRaisesRegex(ValueError, "dimensions"):
            LocalHashingEmbedder(dimensions=16)
        with self.assertRaisesRegex(ValueError, "invalid_or_too_long"):
            LocalHashingEmbedder().encode("   ")

if __name__ == "__main__":
    unittest.main()
