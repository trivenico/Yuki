"""Adapter exposing the verified local Yuki-Core runtime as a Yuki provider.

This initial byte-level model is intentionally text-only and tool-free. It is
not interchangeable with a capable instruction-tuned assistant.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence


class YukiCoreProvider:
    """Run a verified local Yuki-Core release through the provider contract."""

    def __init__(self, model_dir: str | Path, *, device: str = "cpu", runtime_factory=None):
        if not isinstance(model_dir, (str, Path)) or not str(model_dir).strip():
            raise ValueError("yuki_core_model_dir must point to a verified model release directory.")
        if device not in {"cpu", "cuda"}:
            raise ValueError("yuki_core_device must be cpu or cuda.")
        if runtime_factory is None:
            from yuki_training.runtime import YukiCoreRuntime
            runtime_factory = YukiCoreRuntime
        if not callable(runtime_factory):
            raise TypeError("runtime_factory must be callable.")
        self.model_dir = Path(model_dir).expanduser()
        self.device = device
        self.runtime = runtime_factory(self.model_dir, device=device)

    def is_available(self) -> bool:
        return bool(self.runtime.is_available())

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
            raise ValueError("prompt must be non-empty text of at most 20000 characters.")
        return self.runtime.generate(prompt.strip(), max_new_tokens=256)

    def generate_chat(
        self, *, messages: Sequence[Mapping[str, str]],
        tools: Sequence[dict[str, Any]], tool_choice: str = "auto",
    ) -> dict[str, Any]:
        if not isinstance(messages, (list, tuple)) or not messages or len(messages) > 128:
            raise ValueError("messages must contain 1 to 128 entries.")
        if not isinstance(tools, (list, tuple)) or tools:
            raise ValueError("Yuki-Core research runtime does not support tools.")
        if tool_choice not in {"auto", "none"}:
            raise ValueError("Yuki-Core research runtime cannot satisfy a required tool choice.")
        sections = []
        total = 0
        for item in messages:
            if not isinstance(item, Mapping) or set(item) != {"role", "content"}:
                raise ValueError("each message must contain only role and content.")
            role, content = item["role"], item["content"]
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("message role or content is invalid.")
            total += len(content)
            if total > 20_000:
                raise ValueError("Yuki-Core prompt exceeds 20000 characters.")
            sections.append(f"[{role.upper()}]\n{content}")
        prompt = "\n\n".join(sections) + "\n\n[ASSISTANT]\n"
        answer = self.runtime.generate(prompt, max_new_tokens=512)
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Yuki-Core returned an empty response.")
        return {"text": answer.strip(), "tool_calls": {"tool_calls": []}, "finish_reason": "stop"}
