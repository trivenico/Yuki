"""Optional in-process llama.cpp Python bindings adapter.

Install separately with a platform-appropriate llama-cpp-python wheel. Model files must
be supplied by the user; this module never downloads weights or executes model output.
"""
from __future__ import annotations
from pathlib import Path

class LlamaCppPythonProvider:
    def __init__(self, model_path: str, *, n_ctx: int = 4096, n_threads: int | None = None,
                 n_gpu_layers: int = 0, timeout_seconds: int = 60):
        path = Path(model_path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise ValueError("llama.cpp requires an existing user-provided GGUF file.")
        if type(n_ctx) is not int or not 512 <= n_ctx <= 131072:
            raise ValueError("n_ctx must be between 512 and 131072.")
        if n_threads is not None and (type(n_threads) is not int or not 1 <= n_threads <= 256):
            raise ValueError("n_threads must be between 1 and 256.")
        if type(n_gpu_layers) is not int or not 0 <= n_gpu_layers <= 999:
            raise ValueError("n_gpu_layers must be between 0 and 999.")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 1 and 300.")
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise RuntimeError("Install optional native bindings: pip install -r requirements-llama-cpp.txt") from exc
        self.model = Llama(model_path=str(path), n_ctx=n_ctx, n_threads=n_threads,
                           n_gpu_layers=n_gpu_layers, verbose=False)
        self.model_name = path.name
        self.last_usage = {}

    def is_available(self) -> bool:
        return self.model is not None

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
            raise ValueError("Prompt must be non-empty and at most 20,000 characters.")
        response = self.model.create_completion(prompt=prompt, max_tokens=2048, temperature=0.2)
        if not isinstance(response, dict):
            raise RuntimeError("llama.cpp returned an invalid response.")
        usage = response.get("usage", {})
        self.last_usage = {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")} if isinstance(usage, dict) else {}
        choices = response.get("choices")
        text = choices[0].get("text") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        if not isinstance(text, str):
            raise RuntimeError("llama.cpp response did not contain text.")
        return text.strip()
