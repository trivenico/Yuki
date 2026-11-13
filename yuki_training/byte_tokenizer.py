"""Deterministic UTF-8 byte tokenizer owned by Yuki.

This deliberately starts with a simple 256-token vocabulary. It is not
competitive with a trained subword tokenizer, but it has no external model,
merge table, vocabulary download, or hidden dependency.
"""
from __future__ import annotations


class ByteTokenizer:
    vocab_size = 256
    name = "yuki_utf8_byte_v1"

    def encode(self, text: str) -> list[int]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        return list(text.encode("utf-8"))

    def decode(self, token_ids: list[int]) -> str:
        if any(isinstance(token, bool) or not isinstance(token, int) or not 0 <= token < 256 for token in token_ids):
            raise ValueError("byte token IDs must be integers in [0, 255]")
        return bytes(token_ids).decode("utf-8", errors="replace")

    def to_manifest(self) -> dict[str, object]:
        return {"name": self.name, "vocab_size": self.vocab_size, "encoding": "UTF-8 bytes", "special_tokens": []}
