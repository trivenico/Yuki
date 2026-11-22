"""In-process local GGUF inference through the native llama.cpp runtime.

No server, cloud endpoint, model download, or credential is used. The optional
llama-cpp-python package loads the native C++ runtime into this process.
"""
from __future__ import annotations

import importlib
import math
import os
from pathlib import Path
import threading
from typing import Any, Mapping, Sequence

from providers.base import BaseProvider


class LlamaCppProvider(BaseProvider):
    """Run a user-supplied GGUF model locally through llama.cpp."""

    availability_scope = "local_model_file_and_native_runtime"

    def __init__(
        self, model_path: str, *, n_ctx: int = 4096, n_threads: int | None = None,
        n_gpu_layers: int = 0, max_tokens: int = 2048, temperature: float = 0.2,
    ) -> None:
        if not isinstance(model_path, str) or not model_path.strip() or len(model_path) > 4096 or "\x00" in model_path:
            raise ValueError("A bounded local GGUF model path is required.")
        if type(n_ctx) is not int or not 512 <= n_ctx <= 32768:
            raise ValueError("n_ctx must be between 512 and 32768.")
        if n_threads is None:
            n_threads = max(1, min(os.cpu_count() or 1, 64))
        if type(n_threads) is not int or not 1 <= n_threads <= 64:
            raise ValueError("n_threads must be between 1 and 64.")
        if type(n_gpu_layers) is not int or not 0 <= n_gpu_layers <= 256:
            raise ValueError("n_gpu_layers must be between 0 and 256.")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 8192:
            raise ValueError("max_tokens must be between 1 and 8192.")
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be finite and between 0 and 2.")
        candidate = Path(model_path.strip()).expanduser()
        if not candidate.is_absolute():
            raise ValueError("Model path must be absolute.")
        try:
            if candidate.is_symlink():
                raise ValueError("Model path must not be a symbolic link.")
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
        except ValueError:
            raise
        except (OSError, RuntimeError) as exc:
            raise ValueError("Local model file is unavailable.") from exc
        if not resolved.is_file() or not resolved.name.lower().endswith(".gguf") or info.st_size <= 0 or info.st_size > 100 * 1024**3:
            raise ValueError("Model must be a non-empty GGUF file no larger than 100 GiB.")
        self.model_path = resolved
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.n_gpu_layers = n_gpu_layers
        self.max_tokens = max_tokens
        self.temperature = float(temperature)
        self._model: Any = None
        self._lock = threading.RLock()

    def is_available(self) -> bool:
        try:
            if not self.model_path.is_file() or self.model_path.is_symlink():
                return False
            with self.model_path.open("rb") as model_file:
                if not model_file.read(1):
                    return False
            importlib.import_module("llama_cpp")
            return True
        except (ImportError, OSError):
            return False

    def _get_model(self):
        with self._lock:
            if self._model is None:
                if not self.model_path.is_file() or self.model_path.is_symlink():
                    raise RuntimeError("Configured local model file is no longer available.")
                try:
                    native = importlib.import_module("llama_cpp")
                    constructor = getattr(native, "Llama")
                    self._model = constructor(
                        model_path=str(self.model_path), n_ctx=self.n_ctx,
                        n_threads=self.n_threads, n_gpu_layers=self.n_gpu_layers,
                        verbose=False,
                    )
                except Exception as exc:
                    raise RuntimeError("The local llama.cpp runtime could not load the configured model.") from exc
            return self._model

    @staticmethod
    def _validate_messages(messages: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
        if not isinstance(messages, (list, tuple)) or not 1 <= len(messages) <= 128:
            raise ValueError("messages must contain 1 to 128 entries.")
        result = []
        total = 0
        for message in messages:
            if not isinstance(message, Mapping) or set(message) != {"role", "content"}:
                raise ValueError("Each message must contain only role and content.")
            role, content = message["role"], message["content"]
            if not isinstance(role, str) or role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("Message role or content is invalid.")
            total += len(content)
            if total > 100_000:
                raise ValueError("Message transcript exceeds the 100000-character limit.")
            result.append({"role": role, "content": content})
        return result

    @staticmethod
    def _validate_tool_contract(tools, tool_choice: str) -> None:
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("tool_choice is invalid.")
        if tool_choice == "required" and tools:
            raise RuntimeError("This local text-inference adapter does not implement model tool calling; use Yuki's native capability router.")
        # Tool execution is deliberately not delegated to the model. The
        # native capability router and approval gate remain authoritative.

    def generate_chat(self, *, messages, tools=(), tool_choice="auto"):
        normalized = self._validate_messages(messages)
        self._validate_tool_contract(tools, tool_choice)
        with self._lock:
            model = self._get_model()
            try:
                result = model.create_chat_completion(
                    messages=normalized, max_tokens=self.max_tokens,
                    temperature=self.temperature, stream=False,
                )
            except Exception as exc:
                raise RuntimeError("Local llama.cpp inference failed.") from exc
        try:
            choice = result["choices"][0]
            message = choice["message"]
            text = message.get("content", "")
            finish_reason = choice.get("finish_reason", "stop")
            usage = result.get("usage", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Local llama.cpp returned an unsupported response.") from exc
        if not isinstance(text, str) or len(text) > 100_000 or not isinstance(finish_reason, str):
            raise RuntimeError("Local llama.cpp returned invalid response fields.")
        response = {"text": text, "tool_calls": {"tool_calls": []}, "finish_reason": finish_reason}
        if isinstance(usage, dict) and usage:
            prompt_tokens, completion_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if type(prompt_tokens) is int and type(completion_tokens) is int and 0 <= prompt_tokens <= 10_000_000 and 0 <= completion_tokens <= 10_000_000:
                response["usage"] = {
                    "provider": "llama_cpp", "model": self.model_path.name,
                    "input_tokens": prompt_tokens, "output_tokens": completion_tokens,
                    "estimated": False,
                }
        return response

    def generate_chat_stream(self, *, messages, tools=(), tool_choice="auto"):
        normalized = self._validate_messages(messages)
        self._validate_tool_contract(tools, tool_choice)
        text_parts: list[str] = []
        total_chars = 0
        with self._lock:
            model = self._get_model()
            try:
                stream = model.create_chat_completion(
                    messages=normalized, max_tokens=self.max_tokens,
                    temperature=self.temperature, stream=True,
                )
                for chunk in stream:
                    choices = chunk.get("choices") if isinstance(chunk, dict) else None
                    if not isinstance(choices, list) or not choices:
                        raise RuntimeError("Local llama.cpp returned a malformed stream event.")
                    delta = choices[0].get("delta", {})
                    piece = delta.get("content", "") if isinstance(delta, dict) else None
                    if not isinstance(piece, str):
                        raise RuntimeError("Local llama.cpp returned invalid streamed text.")
                    if piece:
                        total_chars += len(piece)
                        if total_chars > 100_000:
                            raise RuntimeError("Local llama.cpp stream exceeded the output limit.")
                        text_parts.append(piece)
                        yield {"type": "text_delta", "text": piece}
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError("Local llama.cpp streaming inference failed.") from exc
        yield {"type": "response", "response": {
            "text": "".join(text_parts), "tool_calls": {"tool_calls": []},
            "finish_reason": "stop",
        }}

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000:
            raise ValueError("Prompt must be non-empty text up to 100000 characters.")
        result = self.generate_chat(
            messages=[{"role": "user", "content": prompt}], tools=[], tool_choice="none",
        )
        return result["text"]

    def close(self) -> None:
        with self._lock:
            model, self._model = self._model, None
            close = getattr(model, "close", None)
            if callable(close):
                close()
