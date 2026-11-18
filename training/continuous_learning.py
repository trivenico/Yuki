"""Low-compute, approval-gated learning coordinator for live Yuki sessions.

Pending corrections and approved examples remain separate. When an encrypted
store is supplied, state changes are durable and atomic. No conversation
history is inspected automatically, and no training or weight changes occur.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable

from training.few_shot_patch import FewShotPatchLayer
from training.sft_data_generator import SFTDataError, SFTDataGenerator, _canonical_messages


class ContinuousLearningError(RuntimeError):
    """A learning action is invalid, unapproved, or unavailable."""


class ContinuousLearningCoordinator:
    """Coordinate explicit memory, correction review, and inference-time examples.

    Consent and approval callbacks must consult authenticated, durable
    application records. The approval verifier must also confirm that a
    persisted proposal/reviewer pair has a valid, non-revoked approval record.
    """

    MAX_PENDING = 100

    def __init__(
        self,
        *,
        memory: Any = None,
        store: Any = None,
        dataset_version: str = "1.0.0",
        consent_verifier: Callable[[str], bool] | None = None,
        approval_verifier: Callable[[str, str], bool] | None = None,
        authorization_snapshot: Callable[[list[str]], dict] | None = None,
    ) -> None:
        if memory is not None and not callable(getattr(memory, "remember", None)):
            raise TypeError("memory must provide remember().")
        if store is not None and not all(
            callable(getattr(store, name, None)) for name in ("read", "update")
        ):
            raise TypeError("store must provide read() and update().")
        if consent_verifier is not None and not callable(consent_verifier):
            raise TypeError("consent_verifier must be callable.")
        if approval_verifier is not None and not callable(approval_verifier):
            raise TypeError("approval_verifier must be callable.")
        if authorization_snapshot is not None and not callable(authorization_snapshot):
            raise TypeError("authorization_snapshot must be callable.")
        self.memory = memory
        self.store = store
        self.generator = SFTDataGenerator(dataset_version=dataset_version)
        self._consent_verifier = consent_verifier
        self._approval_verifier = approval_verifier
        self._authorization_snapshot = authorization_snapshot
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        if self.store is not None:
            self._refresh_persisted_state()

    def _has_consent(self, purpose: str) -> bool:
        if self._consent_verifier is None:
            return False
        try:
            return self._consent_verifier(purpose) is True
        except Exception:
            return False

    def _is_approved(self, proposal_id: str, approved_by: str) -> bool:
        if self._approval_verifier is None:
            return False
        try:
            return self._approval_verifier(proposal_id, approved_by) is True
        except Exception:
            return False

    def _refresh_persisted_state(self) -> dict:
        """Reload encrypted state and verify all authorizations in one bounded audit scan."""
        state = self.store.read()
        proposal_ids = [item["proposal_id"] for item in state["approved"]]
        if self._authorization_snapshot is not None:
            try:
                authorization = self._authorization_snapshot(proposal_ids)
                if (
                    not isinstance(authorization, dict)
                    or type(authorization.get("learning")) is not bool
                    or type(authorization.get("training")) is not bool
                    or not isinstance(authorization.get("approved_proposal_ids"), list)
                ):
                    raise ValueError("invalid authorization snapshot")
                approved_ids = set(authorization["approved_proposal_ids"])
                if not approved_ids.issubset(set(proposal_ids)):
                    raise ValueError("authorization snapshot contains unknown proposals")
            except Exception:
                # A broken/tampered audit log must disable personalization, not
                # silently fall back to stale in-memory approvals.
                authorization = {
                    "learning": False, "training": False,
                    "approved_proposal_ids": [],
                }
                approved_ids = set()
        else:
            authorization = {
                "learning": self._has_consent("learning"),
                "training": self._has_consent("training"),
                "approved_proposal_ids": [],
            }
            approved_ids = set()
            if authorization["training"]:
                for item in state["approved"]:
                    if self._is_approved(item["proposal_id"], item["approved_by"]):
                        approved_ids.add(item["proposal_id"])
        pending = {}
        if authorization["learning"]:
            for item in state["pending"]:
                pending[item["proposal_id"]] = {
                    "messages": [dict(message) for message in item["messages"]],
                    "source_reference": item["source_reference"],
                    "task_type": item["task_type"],
                    "created_at": item["created_at"],
                }
        generator = SFTDataGenerator(dataset_version=self.generator.dataset_version)
        if authorization["training"]:
            for item in state["approved"]:
                if item["proposal_id"] not in approved_ids:
                    continue
                try:
                    generator.add_example(
                        messages=item["messages"],
                        source_reference=item["source_reference"],
                        task_type=item["task_type"],
                        training_consent=True,
                        human_approved=True,
                        approved_by=item["approved_by"],
                    )
                except (SFTDataError, ValueError) as exc:
                    raise ContinuousLearningError(
                        "Persisted approved learning data failed validation."
                    ) from exc
        self._pending = pending
        self.generator = generator
        return authorization

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
            if self.store is not None:
                record = {
                    "proposal_id": proposal_id,
                    "messages": [dict(item) for item in normalized],
                    "source_reference": source_reference,
                    "task_type": task_type,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                def add_pending(state):
                    existing = next(
                        (item for item in state["pending"] if item["proposal_id"] == proposal_id),
                        None,
                    )
                    if existing is not None:
                        return False
                    if len(state["pending"]) >= self.MAX_PENDING:
                        raise ContinuousLearningError("The pending correction review queue is full.")
                    if any(item["proposal_id"] == proposal_id for item in state["approved"]):
                        return False
                    state["pending"].append(record)
                    return True
                try:
                    self.store.update(add_pending)
                    self._refresh_persisted_state()
                except ContinuousLearningError:
                    raise
                except Exception as exc:
                    raise ContinuousLearningError("The encrypted learning store could not save this correction.") from exc
                return proposal_id
            if proposal_id not in self._pending and len(self._pending) >= self.MAX_PENDING:
                raise ContinuousLearningError("The pending correction review queue is full.")
            if proposal_id not in self._pending:
                self._pending[proposal_id] = {
                    "messages": [dict(item) for item in normalized],
                    "source_reference": source_reference,
                    "task_type": task_type,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
        return proposal_id

    def pending_corrections(self) -> list[dict[str, str]]:
        """Expose only bounded review metadata, never source references or transcripts."""
        with self._lock:
            if self.store is not None:
                try:
                    self._refresh_persisted_state()
                except Exception as exc:
                    raise ContinuousLearningError("Pending learning data could not be read safely.") from exc
            if not self._has_consent("learning"):
                return []
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
        if not self._is_approved(proposal_id, approved_by):
            raise ContinuousLearningError("The authenticated reviewer did not approve this proposal.")
        with self._lock:
            if self.store is not None:
                try:
                    state = self.store.read()
                except Exception as exc:
                    raise ContinuousLearningError("The encrypted learning store could not be read.") from exc
                item = next((row for row in state["pending"] if row["proposal_id"] == proposal_id), None)
                if item is None:
                    raise ContinuousLearningError("Correction proposal was not found.")
                candidate = SFTDataGenerator(dataset_version=self.generator.dataset_version)
                try:
                    example_id = candidate.add_example(
                        messages=item["messages"],
                        source_reference=item["source_reference"],
                        task_type=item["task_type"],
                        training_consent=True,
                        human_approved=True,
                        approved_by=approved_by,
                    )
                except (SFTDataError, ValueError) as exc:
                    raise ContinuousLearningError(str(exc)) from exc
                approved_record = {
                    "example_id": example_id,
                    "proposal_id": proposal_id,
                    "messages": [dict(message) for message in item["messages"]],
                    "source_reference": item["source_reference"],
                    "task_type": item["task_type"],
                    "approved_by": approved_by,
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                }
                def commit_approval(latest):
                    current = next(
                        (row for row in latest["pending"] if row["proposal_id"] == proposal_id),
                        None,
                    )
                    if current is None:
                        raise ContinuousLearningError("Correction proposal was already reviewed.")
                    latest["pending"].remove(current)
                    if not any(row["example_id"] == example_id for row in latest["approved"]):
                        latest["approved"].append(approved_record)
                try:
                    self.store.update(commit_approval)
                    self._refresh_persisted_state()
                except ContinuousLearningError:
                    raise
                except Exception as exc:
                    raise ContinuousLearningError("The approved correction could not be committed safely.") from exc
                return example_id

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
            if self.store is not None:
                try:
                    def remove_pending(state):
                        for item in state["pending"]:
                            if item["proposal_id"] == proposal_id:
                                state["pending"].remove(item)
                                return True
                        return False
                    removed = self.store.update(remove_pending)
                    self._refresh_persisted_state()
                    return removed
                except Exception as exc:
                    raise ContinuousLearningError("The correction could not be removed from encrypted storage.") from exc
            return self._pending.pop(proposal_id, None) is not None

    def build_few_shot_patch(
        self,
        query: str,
        *,
        task_type: str | None = None,
        max_examples: int = 3,
        max_chars: int = 6000,
    ) -> str | None:
        """Build a patch only while durable training consent and approvals remain valid."""
        if not self._has_consent("training"):
            return None
        with self._lock:
            if self.store is not None:
                try:
                    self._refresh_persisted_state()
                except Exception as exc:
                    raise ContinuousLearningError("Approved learning data could not be read safely.") from exc
            if not self._has_consent("training"):
                return None
            dataset = self.generator.export_jsonl()
        layer = FewShotPatchLayer(dataset, max_examples=max_examples, max_chars=max_chars)
        return layer.build_patch(query, task_type=task_type)

    def export_sft_jsonl(self) -> str:
        """Return approved examples only while durable training consent is active."""
        if not self._has_consent("training"):
            raise ContinuousLearningError("Verified training consent is required to export this dataset.")
        with self._lock:
            if self.store is not None:
                try:
                    self._refresh_persisted_state()
                except Exception as exc:
                    raise ContinuousLearningError("Approved learning data could not be read safely.") from exc
            if not self._has_consent("training"):
                raise ContinuousLearningError("Training consent was revoked before export.")
            return self.generator.export_jsonl()

    @property
    def approved_example_count(self) -> int:
        with self._lock:
            if self.store is not None:
                self._refresh_persisted_state()
            return len(self.generator)
