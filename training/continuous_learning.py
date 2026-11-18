"""Low-compute, approval-gated learning coordinator for live Yuki sessions.

This composes existing memory and SFT/few-shot primitives. It does not inspect
conversation history, persist raw transcripts, start training, or change weights.
"""
from __future__ import annotations

import hashlib
import re
from threading import RLock
from typing import Any, Callable

from training.few_shot_patch import FewShotPatchLayer
from training.sft_data_generator import SFTDataError, SFTDataGenerator, _canonical_messages


class ContinuousLearningError(RuntimeError):
    """A learning action is invalid, unapproved, or unavailable."""


class ContinuousLearningCoordinator:
    """Coordinate explicit memory, correction review, and inference-time examples.

    Consent and approval callbacks must consult the application's authenticated,
    durable consent/approval records. Caller-supplied booleans alone are not an
    authorization boundary.
    """

    MAX_PENDING = 100

    def __init__(
        self,
        *,
        memory: Any = None,
        dataset_version: str = "1.0.0",
        consent_verifier: Callable[[str], bool] | None = None,
        approval_verifier: Callable[[str, str], bool] | None = None,
    ) -> None:
        if memory is not None and not callable(getattr(memory, "remember", None)):
            raise TypeError("memory must provide remember().")
        if consent_verifier is not None and not callable(consent_verifier):
            raise TypeError("consent_verifier must be callable.")
        if approval_verifier is not None and not callable(approval_verifier):
            raise TypeError("approval_verifier must be callable.")
        self.memory = memory
        self.generator = SFTDataGenerator(dataset_version=dataset_version)
        self._consent_verifier = consent_verifier
        self._approval_verifier = approval_verifier
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def _has_consent(self, purpose: str) -> bool:
        if self._consent_verifier is None:
            return False
        try:
            return self._consent_verifier(purpose) is True
        except Exception:
            return False

    def remember(self, text: str, *, category: str = "note") -> dict:
        """Save only through the existing session-consented memory facade."""
        if self.memory is None:
            raise ContinuousLearningError("No memory store is configured.")
        try:
            return self.memory.remember(text, category=category)
        except Exception as exc:
            raise ContinuousLearningError("Memory could not save this item; check memory consent and storage.") from exc

    def propose_correction(
        self,
        *,
        messages: list[dict[str, str]],
        source_reference: str,
        task_type: str,
        learning_consent: bool,
    ) -> str:
        """Stage a correction only when durable learning consent is verified."""
        if learning_consent is not True or not self._has_consent("learning"):
            raise ContinuousLearningError("A verified, explicit learning-consent record is required.")
        if not isinstance(source_reference, str) or not source_reference.strip() or len(source_reference) > 512:
            raise ValueError("source_reference must be bounded non-empty text.")
        if not isinstance(task_type, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", task_type):
            raise ValueError("task_type must be a stable identifier.")
        normalized = _canonical_messages(messages)
        digest = hashlib.sha256(
            (source_reference + "\0" + task_type + "\0" +
             "\0".join(item["role"] + "\0" + item["content"] for item in normalized)).encode("utf-8")
        ).hexdigest()
        proposal_id = digest[:24]
        with self._lock:
            if proposal_id not in self._pending and len(self._pending) >= self.MAX_PENDING:
                raise ContinuousLearningError("The pending correction review queue is full.")
            if proposal_id not in self._pending:
                self._pending[proposal_id] = {
                    "messages": [dict(item) for item in normalized],
                    "source_reference": source_reference,
                    "task_type": task_type,
                }
        return proposal_id

    def pending_corrections(self) -> list[dict[str, str]]:
        """Expose only bounded review metadata, never the private source reference."""
        with self._lock:
            return [
                {"proposal_id": proposal_id, "task_type": item["task_type"],
                 "message_count": str(len(item["messages"]))}
                for proposal_id, item in sorted(self._pending.items())
            ]

    def approve_correction(
        self,
        proposal_id: str,
        *,
        training_consent: bool,
        approved_by: str,
    ) -> str:
        """Approve one staged example using independently verified consent and authority."""
        if not isinstance(proposal_id, str):
            raise ContinuousLearningError("Correction proposal was not found.")
        if training_consent is not True or not self._has_consent("training"):
            raise ContinuousLearningError("A verified, explicit training-consent record is required.")
        if self._approval_verifier is None:
            raise ContinuousLearningError("No authenticated human-approval verifier is configured.")
        try:
            approved = self._approval_verifier(proposal_id, approved_by) is True
        except Exception:
            approved = False
        if not approved:
            raise ContinuousLearningError("The authenticated reviewer did not approve this proposal.")
        with self._lock:
            item = self._pending.get(proposal_id)
            if item is None:
                raise ContinuousLearningError("Correction proposal was not found.")
            try:
                example_id = self.generator.add_example(
                    messages=item["messages"],
                    source_reference=item["source_reference"],
                    task_type=item["task_type"],
                    training_consent=True,
                    human_approved=True,
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
        with self._lock:
            return self._pending.pop(proposal_id, None) is not None

    def build_few_shot_patch(
        self,
        query: str,
        *,
        task_type: str | None = None,
        max_examples: int = 3,
        max_chars: int = 6000,
    ) -> str | None:
        """Build a patch from approved examples only; no model or policy mutation."""
        with self._lock:
            dataset = self.generator.export_jsonl()
        layer = FewShotPatchLayer(dataset, max_examples=max_examples, max_chars=max_chars)
        return layer.build_patch(query, task_type=task_type)

    def export_sft_jsonl(self) -> str:
        """Return deterministic approved examples in memory; caller owns any file write."""
        with self._lock:
            return self.generator.export_jsonl()

    @property
    def approved_example_count(self) -> int:
        with self._lock:
            return len(self.generator)
