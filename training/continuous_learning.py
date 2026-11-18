"""Low-compute, approval-gated learning coordinator for live Yuki sessions.

This composes existing memory and SFT/few-shot primitives. It does not inspect
conversation history, persist raw transcripts, start training, or change weights.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from training.few_shot_patch import FewShotPatchLayer
from training.sft_data_generator import SFTDataError, SFTDataGenerator


class ContinuousLearningError(RuntimeError):
    """A learning action is invalid, unapproved, or unavailable."""


class ContinuousLearningCoordinator:
    """Coordinate explicit memory, correction review, and inference-time examples."""

    MAX_PENDING = 100

    def __init__(self, *, memory: Any = None, dataset_version: str = "1.0.0") -> None:
        if memory is not None and not callable(getattr(memory, "remember", None)):
            raise TypeError("memory must provide remember().")
        self.memory = memory
        self.generator = SFTDataGenerator(dataset_version=dataset_version)
        self._pending: dict[str, dict[str, Any]] = {}

    def remember(self, text: str, *, category: str = "note") -> dict:
        """Save only through the existing session-consented memory facade."""
        if self.memory is None:
            raise ContinuousLearningError("No memory store is configured.")
        try:
            return self.memory.remember(text, category=category)
        except Exception as exc:
            raise ContinuousLearningError("Memory could not save this item; check memory consent and storage.") from exc

    def propose_correction(self, *, messages: list[dict[str, str]], source_reference: str,
                           task_type: str, learning_consent: bool) -> str:
        """Stage a correction for review; staging never implies training approval."""
        if learning_consent is not True:
            raise ContinuousLearningError("Explicit learning consent is required to stage a correction.")
        if len(self._pending) >= self.MAX_PENDING:
            raise ContinuousLearningError("The pending correction review queue is full.")
        if not isinstance(source_reference, str) or not source_reference.strip() or len(source_reference) > 512:
            raise ValueError("source_reference must be bounded non-empty text.")
        if not isinstance(task_type, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", task_type):
            raise ValueError("task_type must be a stable identifier.")
        # Validate content before holding it in the short-lived review queue.
        from training.sft_data_generator import _canonical_messages
        normalized = _canonical_messages(messages)
        digest = hashlib.sha256(
            (source_reference + "\0" + task_type + "\0" +
             "\0".join(item["role"] + "\0" + item["content"] for item in normalized)).encode("utf-8")
        ).hexdigest()
        proposal_id = digest[:24]
        if proposal_id not in self._pending:
            self._pending[proposal_id] = {
                "messages": [dict(item) for item in normalized],
                "source_reference": source_reference,
                "task_type": task_type,
            }
        return proposal_id

    def pending_corrections(self) -> list[dict[str, str]]:
        """Expose only bounded review metadata, never the private source reference."""
        return [
            {"proposal_id": proposal_id, "task_type": item["task_type"],
             "message_count": str(len(item["messages"]))}
            for proposal_id, item in sorted(self._pending.items())
        ]

    def approve_correction(self, proposal_id: str, *, training_consent: bool,
                           human_approved: bool, approved_by: str) -> str:
        """Approve one staged example for SFT and later few-shot retrieval."""
        if not isinstance(proposal_id, str) or proposal_id not in self._pending:
            raise ContinuousLearningError("Correction proposal was not found.")
        item = self._pending[proposal_id]
        try:
            example_id = self.generator.add_example(
                messages=item["messages"],
                source_reference=item["source_reference"],
                task_type=item["task_type"],
                training_consent=training_consent,
                human_approved=human_approved,
                approved_by=approved_by,
            )
        except (SFTDataError, ValueError) as exc:
            raise ContinuousLearningError(str(exc)) from exc
        del self._pending[proposal_id]
        return example_id

    def reject_correction(self, proposal_id: str) -> bool:
        """Discard a staged correction without exporting it."""
        if not isinstance(proposal_id, str):
            return False
        return self._pending.pop(proposal_id, None) is not None

    def build_few_shot_patch(self, query: str, *, task_type: str | None = None,
                             max_examples: int = 3, max_chars: int = 6000) -> str | None:
        """Build a patch from approved examples only; no model or policy mutation."""
        layer = FewShotPatchLayer(
            self.generator.export_jsonl(), max_examples=max_examples, max_chars=max_chars,
        )
        return layer.build_patch(query, task_type=task_type)

    def export_sft_jsonl(self) -> str:
        """Return deterministic approved examples in memory; caller owns any file write."""
        return self.generator.export_jsonl()

    @property
    def approved_example_count(self) -> int:
        return len(self.generator)
