"""Explicit-consent SFT example curation and in-memory JSONL export."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable

MAX_EXAMPLES = 10_000
MAX_MESSAGES = 32
MAX_MESSAGE_CHARS = 16_000
MAX_EXAMPLE_CHARS = 48_000
_ALLOWED_ROLES = {"system", "user", "assistant"}
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(?:api[_ -]?key|password|client[_ -]?secret|access[_ -]?token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

class SFTDataError(ValueError):
    """An example is not safe or eligible for supervised fine-tuning."""

@dataclass(frozen=True)
class SFTExample:
    example_id: str
    messages: tuple[dict[str, str], ...]
    source_digest: str
    approval_digest: str
    task_type: str

def _canonical_messages(messages: Iterable[dict[str, Any]]) -> tuple[dict[str, str], ...]:
    if not isinstance(messages, (list, tuple)) or not 2 <= len(messages) <= MAX_MESSAGES:
        raise SFTDataError("messages must contain between 2 and 32 entries")
    normalized, total = [], 0
    for item in messages:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            raise SFTDataError("each message must contain only role and content")
        role, content = item["role"], item["content"]
        if not isinstance(role, str) or role not in _ALLOWED_ROLES or not isinstance(content, str) or not content.strip():
            raise SFTDataError("messages require a supported role and non-empty text")
        if len(content) > MAX_MESSAGE_CHARS:
            raise SFTDataError("a message exceeds the 16,000-character limit")
        if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
            raise SFTDataError("possible credential or private-key material detected")
        total += len(content)
        normalized.append({"role": role, "content": content.strip()})
    if total > MAX_EXAMPLE_CHARS:
        raise SFTDataError("example exceeds the 48,000-character limit")
    if normalized[-1]["role"] != "assistant":
        raise SFTDataError("the final message must be an assistant answer")
    if not any(message["role"] == "user" for message in normalized):
        raise SFTDataError("example must contain at least one user message")
    if any(message["role"] == "system" for message in normalized[1:]):
        raise SFTDataError("system message, when present, must be first")
    return tuple(normalized)

class SFTDataGenerator:
    """Curate approved examples and produce deterministic JSONL exports."""
    def __init__(self, *, dataset_version: str = "1.0.0"):
        if not isinstance(dataset_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", dataset_version):
            raise ValueError("dataset_version must use semantic version format")
        self.dataset_version = dataset_version
        self._examples: dict[str, SFTExample] = {}

    def add_example(self, *, messages: list[dict[str, str]], source_reference: str,
                    task_type: str, training_consent: bool, human_approved: bool,
                    approved_by: str) -> str:
        if training_consent is not True or human_approved is not True:
            raise SFTDataError("explicit training consent and human approval are required")
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 160:
            raise SFTDataError("approved_by must identify the approving account")
        if not isinstance(source_reference, str) or not source_reference.strip() or len(source_reference) > 512:
            raise SFTDataError("source_reference must be a bounded non-empty reference")
        if not isinstance(task_type, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", task_type):
            raise SFTDataError("task_type must be a short stable identifier")
        normalized = _canonical_messages(messages)
        canonical = json.dumps({"task_type": task_type, "messages": normalized}, ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        example_id = digest[:24]
        if example_id in self._examples:
            return example_id
        if len(self._examples) >= MAX_EXAMPLES:
            raise SFTDataError("dataset example limit reached")
        source_digest = hashlib.sha256(source_reference.encode("utf-8")).hexdigest()
        approval_digest = hashlib.sha256(approved_by.strip().encode("utf-8")).hexdigest()
        self._examples[example_id] = SFTExample(example_id, normalized, source_digest, approval_digest, task_type)
        return example_id

    def export_jsonl(self) -> str:
        rows = []
        for example in sorted(self._examples.values(), key=lambda item: item.example_id):
            rows.append(json.dumps({
                "schema_version": 1, "messages": list(example.messages),
                "metadata": {"example_id": example.example_id, "dataset_version": self.dataset_version,
                             "task_type": example.task_type, "source_digest": example.source_digest,
                             "approval_digest": example.approval_digest, "consent_recorded": True,
                             "approval_state": "human_approved"},
            }, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(rows) + ("\n" if rows else "")

    def __len__(self) -> int:
        return len(self._examples)
