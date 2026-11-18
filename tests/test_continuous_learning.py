import json
import pytest

from training.continuous_learning import ContinuousLearningCoordinator, ContinuousLearningError


def _messages(answer="Use a focused unit test and assert the expected result."):
    return [
        {"role": "user", "content": "How should I test this Python function?"},
        {"role": "assistant", "content": answer},
    ]


class FakeMemory:
    def __init__(self):
        self.enabled = False
        self.items = []

    def remember(self, text, *, category="note"):
        if not self.enabled:
            raise PermissionError("memory consent is disabled")
        item = {"text": text, "category": category}
        self.items.append(item)
        return item


def test_correction_requires_explicit_learning_consent():
    learning = ContinuousLearningCoordinator()
    with pytest.raises(ContinuousLearningError):
        learning.propose_correction(messages=_messages(), source_reference="conversation:1",
                                    task_type="coding", learning_consent=False)
    assert learning.pending_corrections() == []
    assert learning.approved_example_count == 0


def test_staged_correction_is_not_exported_until_separately_approved():
    learning = ContinuousLearningCoordinator()
    proposal = learning.propose_correction(messages=_messages(), source_reference="conversation:1",
                                            task_type="coding", learning_consent=True)
    assert len(learning.pending_corrections()) == 1
    assert learning.export_sft_jsonl() == ""
    with pytest.raises(ContinuousLearningError):
        learning.approve_correction(proposal, training_consent=False,
                                    human_approved=True, approved_by="owner")
    assert len(learning.pending_corrections()) == 1
    example_id = learning.approve_correction(proposal, training_consent=True,
                                             human_approved=True, approved_by="owner")
    assert len(example_id) == 24
    assert learning.pending_corrections() == []
    assert learning.approved_example_count == 1
    row = json.loads(learning.export_sft_jsonl())
    assert row["metadata"]["approval_state"] == "human_approved"


def test_approved_correction_is_available_to_few_shot_without_training():
    learning = ContinuousLearningCoordinator()
    proposal = learning.propose_correction(messages=_messages(), source_reference="task:42",
                                            task_type="coding", learning_consent=True)
    learning.approve_correction(proposal, training_consent=True,
                                human_approved=True, approved_by="owner")
    patch = learning.build_few_shot_patch("Python function unit test", task_type="coding")
    assert patch is not None
    assert "approved historical examples" in patch
    assert learning.approved_example_count == 1


def test_rejected_correction_never_enters_dataset():
    learning = ContinuousLearningCoordinator()
    proposal = learning.propose_correction(messages=_messages(), source_reference="task:43",
                                            task_type="coding", learning_consent=True)
    assert learning.reject_correction(proposal) is True
    assert learning.reject_correction(proposal) is False
    assert learning.export_sft_jsonl() == ""


def test_memory_write_uses_existing_memory_consent():
    memory = FakeMemory()
    learning = ContinuousLearningCoordinator(memory=memory)
    with pytest.raises(ContinuousLearningError):
        learning.remember("Prefer concise reports", category="preference")
    memory.enabled = True
    result = learning.remember("Prefer concise reports", category="preference")
    assert result["category"] == "preference"
    assert len(memory.items) == 1
