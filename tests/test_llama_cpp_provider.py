import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from providers.factory import create_provider
from providers.llama_cpp import LlamaCppProvider


class FakeLlama:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.closed = False
        type(self).instances.append(self)

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return iter([
                {"choices": [{"delta": {"content": "Local "}}]},
                {"choices": [{"delta": {"content": "Yuki"}}]},
            ])
        return {
            "choices": [{"message": {"content": "Local Yuki"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }

    def close(self):
        self.closed = True


class LlamaCppProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model_path = Path(self.temp.name) / "test-model.gguf"
        self.model_path.write_bytes(b"test model fixture")
        FakeLlama.instances.clear()
        self.native_module = types.ModuleType("llama_cpp")
        self.native_module.Llama = FakeLlama
        self.module_patch = patch.dict(sys.modules, {"llama_cpp": self.native_module})
        self.module_patch.start()

    def tearDown(self):
        self.module_patch.stop()
        self.temp.cleanup()

    def provider(self, **kwargs):
        return LlamaCppProvider(str(self.model_path), **kwargs)

    def test_factory_constructs_in_process_local_provider(self):
        provider = create_provider(
            {"provider": "llama_cpp", "llama_cpp_model_path": str(self.model_path)},
            environ={},
        )
        self.assertIsInstance(provider, LlamaCppProvider)
        self.assertTrue(provider.is_available())

    def test_generation_loads_native_runtime_lazily_and_preserves_usage(self):
        provider = self.provider(n_ctx=2048, n_threads=2, n_gpu_layers=0)
        self.assertEqual(FakeLlama.instances, [])
        response = provider.generate_chat(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[], tool_choice="none",
        )
        self.assertEqual(response["text"], "Local Yuki")
        self.assertEqual(response["usage"]["provider"], "llama_cpp")
        self.assertEqual(FakeLlama.instances[0].kwargs["n_ctx"], 2048)
        self.assertEqual(FakeLlama.instances[0].kwargs["n_threads"], 2)

    def test_streaming_uses_native_chunks_and_final_envelope(self):
        provider = self.provider()
        events = list(provider.generate_chat_stream(
            messages=[{"role": "user", "content": "Hello"}], tools=[],
        ))
        self.assertEqual(events[0], {"type": "text_delta", "text": "Local "})
        self.assertEqual(events[1], {"type": "text_delta", "text": "Yuki"})
        self.assertEqual(events[-1]["response"]["text"], "Local Yuki")
        self.assertEqual(events[-1]["response"]["tool_calls"], {"tool_calls": []})

    def test_model_tool_execution_is_never_enabled(self):
        provider = self.provider()
        with self.assertRaisesRegex(RuntimeError, "native capability router"):
            provider.generate_chat(
                messages=[{"role": "user", "content": "run"}],
                tools=[{"type": "function", "function": {"name": "run"}}],
                tool_choice="required",
            )
        self.assertEqual(FakeLlama.instances, [])

    def test_model_file_must_be_regular_gguf_and_not_symlink(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            LlamaCppProvider(str(Path(self.temp.name) / "missing.gguf"))
        non_gguf = Path(self.temp.name) / "model.bin"
        non_gguf.write_bytes(b"model")
        with self.assertRaisesRegex(ValueError, "GGUF"):
            LlamaCppProvider(str(non_gguf))
        link = Path(self.temp.name) / "linked.gguf"
        try:
            link.symlink_to(self.model_path)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable on this platform")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            LlamaCppProvider(str(link))

    def test_model_path_must_be_absolute(self):
        with self.assertRaisesRegex(ValueError, "must be absolute"):
            LlamaCppProvider("relative-model.gguf")

    def test_model_path_rejects_nul_bytes(self):
        with self.assertRaisesRegex(ValueError, "model path"):
            LlamaCppProvider(str(self.model_path) + "\x00")

    def test_bounds_and_cleanup(self):
        with self.assertRaises(ValueError):
            self.provider(n_ctx=100_000)
        with self.assertRaisesRegex(ValueError, "cannot exceed n_ctx"):
            self.provider(n_ctx=512, max_tokens=1024)
        provider = self.provider()
        provider.generate("hello")
        model = FakeLlama.instances[-1]
        provider.close()
        self.assertTrue(model.closed)
        self.assertIsNone(provider._model)

    def test_invalid_message_and_output_contracts_fail_closed(self):
        provider = self.provider()
        with self.assertRaises(ValueError):
            provider.generate_chat(messages=[{"role": "developer", "content": "x"}], tools=[])
        with self.assertRaises(ValueError):
            provider.generate_chat(messages=[{"role": "user", "content": "x" * 100_001}], tools=[])


if __name__ == "__main__":
    unittest.main()
