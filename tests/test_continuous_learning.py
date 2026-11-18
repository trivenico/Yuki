import copy
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


class FakeDurableLearningStore:
    def __init__(self):
        self.state = {"schema_version": 1, "pending": [], "approved": []}

    def read(self):
        return copy.deepcopy(self.state)

    def update(self, mutator):
        state = copy.deepcopy(self.state)
        result = mutator(state)
        self.state = state
        return result


def _learning(memory=None, *, consent=True, approval=True, store=None):
    consent_verifier = consent if callable(consent) else (lambda purpose: consent)
    return ContinuousLearningCoordinator(
        memory=memory,
        store=store,
        consent_verifier=consent_verifier,
        approval_verifier=lambda proposal_id, actor: approval and actor == "owner",
    )


class ContinuousLearningTests(unittest.TestCase):
    def test_missing_consent_verifier_fails_closed(self):
        learning = ContinuousLearningCoordinator()
        with self.assertRaises(ContinuousLearningError):
            learning.propose_correction(messages=_messages(), source_reference="conversation:1",
                                        task_type="coding", learning_consent=True)

    def test_correction_requires_explicit_and_verified_learning_consent(self):
        learning = _learning(consent=False)
        with self.assertRaises(ContinuousLearningError):
            learning.propose_correction(messages=_messages(), source_reference="conversation:1",
                                        task_type="coding", learning_consent=True)
        self.assertEqual(learning.pending_corrections(), [])
        self.assertEqual(learning.approved_example_count, 0)

    def test_staged_correction_is_not_exported_until_separately_approved(self):
        learning = _learning()
        proposal = learning.propose_correction(messages=_messages(), source_reference="conversation:1",
                                                task_type="coding", learning_consent=True)
        self.assertEqual(len(learning.pending_corrections()), 1)
        self.assertEqual(learning.export_sft_jsonl(), "")
        with self.assertRaises(ContinuousLearningError):
            learning.approve_correction(proposal, training_consent=False, approved_by="owner")
        with self.assertRaises(ContinuousLearningError):
            learning.approve_correction(proposal, training_consent=True, approved_by="untrusted")
        self.assertEqual(len(learning.pending_corrections()), 1)
        example_id = learning.approve_correction(proposal, training_consent=True, approved_by="owner")
        self.assertEqual(len(example_id), 24)
        self.assertEqual(learning.pending_corrections(), [])
        self.assertEqual(learning.approved_example_count, 1)
        row = json.loads(learning.export_sft_jsonl())
        self.assertEqual(row["metadata"]["approval_state"], "human_approved")

    def test_training_consent_must_be_verified_separately(self):
        learning = _learning(consent=False)
        proposal = _learning().propose_correction(
            messages=_messages(), source_reference="task:consent", task_type="coding", learning_consent=True,
        )
        with self.assertRaises(ContinuousLearningError):
            learning.approve_correction(proposal, training_consent=True, approved_by="owner")

    def test_approved_correction_is_available_to_few_shot_without_training(self):
        learning = _learning()
        proposal = learning.propose_correction(messages=_messages(), source_reference="task:42",
                                                task_type="coding", learning_consent=True)
        learning.approve_correction(proposal, training_consent=True, approved_by="owner")
        patch = learning.build_few_shot_patch("Python function unit test", task_type="coding")
        self.assertIsNotNone(patch)
        self.assertIn("approved historical examples", patch)
        self.assertEqual(learning.approved_example_count, 1)

    def test_pending_review_queue_is_bounded(self):
        learning = _learning()
        for index in range(learning.MAX_PENDING):
            learning.propose_correction(
                messages=_messages(answer=f"Answer {index} with a distinct result."),
                source_reference=f"task:{index}", task_type="coding", learning_consent=True,
            )
        with self.assertRaises(ContinuousLearningError):
            learning.propose_correction(
                messages=_messages(answer="One more distinct answer."),
                source_reference="task:overflow", task_type="coding", learning_consent=True,
            )
        self.assertEqual(len(learning.pending_corrections()), learning.MAX_PENDING)

    def test_review_api_exposes_sample_but_not_private_source_reference(self):
        learning = _learning()
        proposal = learning.propose_correction(
            messages=_messages(), source_reference="private://source-reference",
            task_type="coding", learning_consent=True,
        )
        review = learning.correction_for_review(proposal)
        self.assertEqual(review["proposal_id"], proposal)
        self.assertEqual(review["messages"], _messages())
        self.assertNotIn("source_reference", review)
        self.assertNotIn("private://source-reference", json.dumps(review))

    def test_review_api_hides_samples_when_learning_consent_is_revoked(self):
        store = FakeDurableLearningStore()
        learning = _learning(store=store)
        proposal = learning.propose_correction(
            messages=_messages(), source_reference="task:review-consent",
            task_type="coding", learning_consent=True,
        )
        revoked = _learning(store=store, consent=False)
        self.assertIsNone(revoked.correction_for_review(proposal))

    def test_persisted_correction_survives_restart_and_approval(self):
        store = FakeDurableLearningStore()
        first = _learning(store=store)
        proposal = first.propose_correction(
            messages=_messages(), source_reference="task:persist", task_type="coding",
            learning_consent=True,
        )
        self.assertEqual(len(store.read()["pending"]), 1)

        restarted = _learning(store=store)
        self.assertEqual(len(restarted.pending_corrections()), 1)
        example_id = restarted.approve_correction(
            proposal, training_consent=True, approved_by="owner",
        )
        self.assertEqual(len(store.read()["pending"]), 0)
        self.assertEqual(store.read()["approved"][0]["example_id"], example_id)

        after_restart = _learning(store=store)
        self.assertEqual(after_restart.approved_example_count, 1)
        self.assertIsNotNone(after_restart.build_few_shot_patch("Python unit test"))

    def test_learning_store_failure_does_not_block_live_few_shot_call(self):
        class FailingStore(FakeDurableLearningStore):
            fail = False

            def read(self):
                if self.fail:
                    raise OSError("encrypted file unavailable")
                return super().read()

        store = FailingStore()
        learning = _learning(store=store)
        store.fail = True
        self.assertIsNone(learning.build_few_shot_patch("Python unit test"))
        self.assertIn("personalization is paused", learning.last_learning_error)

    def test_revoked_training_consent_hides_persisted_examples(self):
        store = FakeDurableLearningStore()
        learning = _learning(store=store)
        proposal = learning.propose_correction(
            messages=_messages(), source_reference="task:revoked", task_type="coding",
            learning_consent=True,
        )
        learning.approve_correction(proposal, training_consent=True, approved_by="owner")
        revoked = _learning(store=store, consent=False)
        self.assertIsNone(revoked.build_few_shot_patch("Python unit test"))
        with self.assertRaises(ContinuousLearningError):
            revoked.export_sft_jsonl()
        self.assertEqual(len(store.read()["approved"]), 1)

    def test_personalization_and_training_consent_control_separate_paths(self):
        store = FakeDurableLearningStore()
        enabled = _learning(store=store)
        proposal = enabled.propose_correction(
            messages=_messages(), source_reference="task:separate-consent",
            task_type="coding", learning_consent=True,
        )
        enabled.approve_correction(proposal, training_consent=True, approved_by="owner")

        personalization_only = _learning(
            store=store,
            consent=lambda purpose: purpose == "learning",
        )
        self.assertIsNotNone(personalization_only.build_few_shot_patch("Python unit test"))
        with self.assertRaises(ContinuousLearningError):
            personalization_only.export_sft_jsonl()

        training_only = _learning(
            store=store,
            consent=lambda purpose: purpose == "training",
        )
        self.assertIsNone(training_only.build_few_shot_patch("Python unit test"))
        self.assertIn("human_approved", training_only.export_sft_jsonl())

    def test_revoked_reviewer_approval_hides_persisted_examples(self):
        store = FakeDurableLearningStore()
        learning = _learning(store=store)
        proposal = learning.propose_correction(
            messages=_messages(), source_reference="task:approval-revoked", task_type="coding",
            learning_consent=True,
        )
        learning.approve_correction(proposal, training_consent=True, approved_by="owner")
        revoked = _learning(store=store, approval=False)
        self.assertIsNone(revoked.build_few_shot_patch("Python unit test"))
        self.assertEqual(revoked.approved_example_count, 0)

    def test_approval_is_recorded_only_inside_transaction_for_existing_pending_item(self):
        store = FakeDurableLearningStore()
        learning = _learning(store=store)
        calls = []
        with self.assertRaises(ContinuousLearningError):
            learning.approve_correction(
                "f" * 24,
                training_consent=True,
                approved_by="owner",
                approval_recorder=lambda proposal_id, approved: calls.append((proposal_id, approved)),
            )
        self.assertEqual(calls, [])

        proposal = learning.propose_correction(
            messages=_messages(), source_reference="task:approval-lock",
            task_type="coding", learning_consent=True,
        )
        learning.approve_correction(
            proposal,
            training_consent=True,
            approved_by="owner",
            approval_recorder=lambda proposal_id, approved: calls.append((proposal_id, approved)),
        )
        self.assertEqual(calls, [(proposal, True)])

    def test_delete_all_learning_data_revokes_before_atomic_clear(self):
        store = FakeDurableLearningStore()
        learning = _learning(store=store)
        proposal = learning.propose_correction(
            messages=_messages(), source_reference="task:delete",
            task_type="coding", learning_consent=True,
        )
        learning.approve_correction(proposal, training_consent=True, approved_by="owner")

        def fail_revoke(_proposal_id):
            raise OSError("audit unavailable")

        with self.assertRaises(ContinuousLearningError):
            learning.delete_all_learning_data(revoke_approval=fail_revoke)
        self.assertEqual(len(store.read()["approved"]), 1)

        revoked = []
        counts = learning.delete_all_learning_data(revoke_approval=revoked.append)
        self.assertEqual(counts, {"pending": 0, "approved": 1})
        self.assertEqual(revoked, [proposal])
        self.assertEqual(store.read()["approved"], [])
        self.assertEqual(store.read()["pending"], [])

    def test_rejected_correction_never_enters_dataset(self):
        learning = _learning()
        proposal = learning.propose_correction(messages=_messages(), source_reference="task:43",
                                                task_type="coding", learning_consent=True)
        self.assertTrue(learning.reject_correction(proposal))
        self.assertFalse(learning.reject_correction(proposal))
        self.assertEqual(learning.export_sft_jsonl(), "")

    def test_memory_write_uses_existing_memory_consent(self):
        memory = FakeMemory()
        learning = _learning(memory)
        with self.assertRaises(ContinuousLearningError):
            learning.remember("Prefer concise reports", category="preference")
        memory.enabled = True
        result = learning.remember("Prefer concise reports", category="preference")
        self.assertEqual(result["category"], "preference")
        self.assertEqual(len(memory.items), 1)


if __name__ == "__main__":
    unittest.main()
