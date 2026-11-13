import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_HAS_RUNTIME_DEPS = importlib.util.find_spec("torch") is not None and importlib.util.find_spec("safetensors") is not None


@unittest.skipUnless(_HAS_RUNTIME_DEPS, "optional Yuki training/runtime dependencies are not installed")
class YukiCoreRuntimeTests(unittest.TestCase):
    def setUp(self):
        from safetensors.torch import save_file
        from yuki_training.decoder import YukiDecoder

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        config = {
            "vocab_size": 256, "block_size": 32, "n_embd": 8,
            "n_head": 2, "n_layer": 1, "dropout": 0.0,
            "model_id": "yuki-core", "version": "0.1.0",
        }
        model = YukiDecoder(
            vocab_size=256, block_size=32, n_embd=8,
            n_head=2, n_layer=1, dropout=0.0,
        )
        weights = self.root / "weights.safetensors"
        save_file(
            {key: value.detach().cpu().contiguous() for key, value in model.state_dict().items()},
            str(weights),
            metadata={"format": "pt", "architecture": "yuki_decoder_v1",
                      "model_id": "yuki-core", "version": "0.1.0", "seed": "7"},
        )
        tokenizer = {
            "name": "yuki_utf8_byte_v1", "vocab_size": 256,
            "encoding": "UTF-8 bytes", "special_tokens": [],
        }
        (self.root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        (self.root / "tokenizer.json").write_text(json.dumps(tokenizer), encoding="utf-8")
        self.manifest = {
            "schema_version": 1, "model_id": "yuki-core", "version": "0.1.0",
            "family": "language", "architecture": "yuki_decoder_v1",
            "origin": "random_init", "license": "reviewed-test-fixture",
            "training_data": "synthetic test fixture only",
            "tasks": ["next_token_prediction"],
            "artifacts": {
                "weights": {"path": "weights.safetensors", "sha256": self._hash(weights)},
                "tokenizer": {"path": "tokenizer.json", "sha256": self._hash(self.root / "tokenizer.json")},
                "config": {"path": "config.json", "sha256": self._hash(self.root / "config.json")},
            },
        }
        self._write_manifest()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _hash(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_manifest(self):
        (self.root / "yuki-core.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_loads_verified_checkpoint_and_generates_bounded_output(self):
        from yuki_training.runtime import YukiCoreRuntime
        runtime = YukiCoreRuntime(self.root)
        self.assertTrue(runtime.is_available())
        self.assertEqual(runtime.model_id, "yuki-core")
        self.assertLessEqual(len(runtime.generate("Yuki", max_new_tokens=4)), 4)

    def test_rejects_tampered_weights(self):
        from yuki_training.runtime import YukiCoreRuntime, YukiCoreRuntimeError
        (self.root / "weights.safetensors").write_bytes(b"tampered")
        with self.assertRaisesRegex(YukiCoreRuntimeError, "integrity"):
            YukiCoreRuntime(self.root)

    def test_rejects_unknown_architecture_before_loading_weights(self):
        from yuki_training.runtime import YukiCoreRuntime, YukiCoreRuntimeError
        self.manifest["architecture"] = "untrusted_python_architecture"
        self._write_manifest()
        with self.assertRaisesRegex(YukiCoreRuntimeError, "architecture"):
            YukiCoreRuntime(self.root)

    def test_rejects_artifact_path_escape(self):
        from yuki_training.runtime import YukiCoreRuntime, YukiCoreRuntimeError
        outside = self.root.parent / "outside-yuki-test.bin"
        outside.write_bytes(b"outside")
        try:
            self.manifest["artifacts"]["weights"]["path"] = "../outside-yuki-test.bin"
            self.manifest["artifacts"]["weights"]["sha256"] = self._hash(outside)
            self._write_manifest()
            with self.assertRaisesRegex(YukiCoreRuntimeError, "escapes"):
                YukiCoreRuntime(self.root)
        finally:
            outside.unlink(missing_ok=True)

    def test_rejects_model_dimensions_above_runtime_parameter_budget(self):
        from yuki_training.runtime import YukiCoreRuntime, YukiCoreRuntimeError
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.update({"n_embd": 4096, "n_head": 64, "n_layer": 64, "block_size": 8192})
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.manifest["artifacts"]["config"]["sha256"] = self._hash(config_path)
        self._write_manifest()
        with self.assertRaisesRegex(YukiCoreRuntimeError, "50-million-parameter"):
            YukiCoreRuntime(self.root)

    def test_rejects_oversized_generation_request(self):
        from yuki_training.runtime import YukiCoreRuntime
        runtime = YukiCoreRuntime(self.root)
        with self.assertRaisesRegex(ValueError, "max_new_tokens"):
            runtime.generate("Yuki", max_new_tokens=1025)


if __name__ == "__main__":
    unittest.main()
