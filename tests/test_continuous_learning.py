import json
import unittest

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


class ContinuousLearningTests(unittest.TestCase):
    def test_correction_requires_explicit_learning_consent(self):
        learning = ContinuousLearningCoordinator()
        with self.assertRaises(ContinuousLearningError):
            learning.propose_correction(messages=_messages(), source_reference="conversation:1",
                                        task_type="coding", learning_consent=False)
        self.assertEqual(learning.pending_corrections(), [])
        self.assertEqual(learning.approved_example_count, 0)

    def test_staged_correction_is_not_exported_until_separately_approved(self):
        learning = ContinuousLearningCoordinator()
        proposal = learning.propose_correction(messages=_messages(), source_reference="conversation:1",
                                                task_type="coding", learning_consent=True)
        self.assertEqual(len(learning.pending_corrections()), 1)
        self.assertEqual(learning.export_sft_jsonl(), "")
        with self.assertRaises(ContinuousLearningError):
            learning.approve_correction(proposal, training_consent=False,
                                        human_approved=True, approved_by="owner")
        self.assertEqual(len(learning.pending_corrections()), 1)
        example_id = learning.approve_correction(proposal, training_consent=True,
                                                 human_approved=True, approved_by="owner")
        self.assertEqual(len(example_id), 24)
        self.assertEqual(learning.pending_corrections(), [])
        self.assertEqual(learning.approved_example_count, 1)
        row = json.loads(learning.export_sft_jsonl())
        self.assertEqual(row["metadata"]["approval_state"], "human_approved")

    def test_approved_correction_is_available_to_few_shot_without_training(self):
        learning = ContinuousLearningCoordinator()
        proposal = learning.propose_correction(messages=_messages(), source_reference="task:42",
                                                task_type="coding", learning_consent=True)
        learning.approve_correction(proposal, training_consent=True,
                                    human_approved=True, approved_by="owner")
        patch = learning.build_few_shot_patch("Python function unit test", task_type="coding")
        self.assertIsNotNone(patch)
        self.assertIn("approved historical examples", patch)
        self.assertEqual(learning.approved_example_count, 1)

    def test_rejected_correction_never_enters_dataset(self):
        learning = ContinuousLearningCoordinator()
        proposal = learning.propose_correction(messages=_messages(), source_reference="task:43",
                                                task_type="coding", learning_consent=True)
        self.assertTrue(learning.reject_correction(proposal))
        self.assertFalse(learning.reject_correction(proposal))
        self.assertEqual(learning.export_sft_jsonl(), "")

    def test_memory_write_uses_existing_memory_consent(self):
        memory = FakeMemory()
        learning = ContinuousLearningCoordinator(memory=memory)
        with self.assertRaises(ContinuousLearningError):
            learning.remember("Prefer concise reports", category="preference")
        memory.enabled = True
        result = learning.remember("Prefer concise reports", category="preference")
        self.assertEqual(result["category"], "preference")
        self.assertEqual(len(memory.items), 1)


if __name__ == "__main__":
    unittest.main()
