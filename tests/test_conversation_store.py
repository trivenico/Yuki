import tempfile
from unittest.mock import patch
import unittest
from pathlib import Path

from core.conversation_store import (
    ConversationConflict,
    ConversationNotFound,
    ConversationStore,
)


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.tempdir.name) / "conversations.sqlite3")
        self.owner = "user-123"
        self.other_owner = "user-456"
        self.conversation = self.store.create_conversation(owner_id=self.owner)

    def tearDown(self):
        self.tempdir.cleanup()

    def _record_provider_evidence(self, *, owner_id, conversation_id, request_id, call_id, evidence):
        import uuid
        token = uuid.uuid4().hex
        self.store.acquire_provider_reconciliation_lease(
            owner_id=owner_id, conversation_id=conversation_id,
            request_id=request_id, call_id=call_id, lease_token=token,
        )
        try:
            return self.store.record_provider_outcome_evidence(
                owner_id=owner_id, conversation_id=conversation_id,
                request_id=request_id, call_id=call_id, evidence=evidence,
                reconciliation_lease_token=token,
            )
        finally:
            execution = next(
                item for item in self.store.list_tool_executions(
                    owner_id=owner_id, conversation_id=conversation_id, request_id=request_id,
                ) if item["call_id"] == call_id
            )
            self.store.release_provider_reconciliation_lease(
                owner_id=owner_id, execution_id=execution["execution_id"], lease_token=token,
            )

    def append(self, role, content, key, version):
        return self.store.append_message(
            owner_id=self.owner,
            conversation_id=self.conversation["conversation_id"],
            role=role,
            content=content,
            idempotency_key=key,
            expected_version=version,
        )

    def test_expired_lease_can_be_reclaimed_and_old_token_cannot_release_new_lease(self):
        conversation_id = self.conversation["conversation_id"]
        with patch("core.conversation_store.time.time", return_value=1_000.0):
            first = self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="first-worker", ttl_seconds=5,
            )
        self.assertEqual(first["expires_at"], 1_005.0)

        with patch("core.conversation_store.time.time", return_value=1_006.0):
            second = self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="replacement-worker", ttl_seconds=30,
            )
            self.assertFalse(self.store.release_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="first-worker",
            ))
            with self.assertRaisesRegex(ConversationConflict, "another generation is already active"):
                self.store.acquire_generation_lease(
                    owner_id=self.owner, conversation_id=conversation_id,
                    lease_token="third-worker", ttl_seconds=30,
                )
            self.assertTrue(self.store.release_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="replacement-worker",
            ))
        self.assertEqual(second["expires_at"], 1_036.0)

    def test_generation_finalization_requires_active_matching_lease(self):
        conversation_id = self.conversation["conversation_id"]
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id,
            role="user", content="hello", idempotency_key="user:finalize-fence",
            expected_version=0, generation_request_id="finalize-fence",
        )
        with patch("core.conversation_store.time.time", return_value=4_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="active-worker", ttl_seconds=5,
            )
            with patch("core.conversation_store.time.time", return_value=4_006.0):
                self.store.acquire_generation_lease(
                    owner_id=self.owner, conversation_id=conversation_id,
                    lease_token="replacement-worker", ttl_seconds=30,
                )
                self.assertFalse(self.store.finish_generation(
                    owner_id=self.owner, conversation_id=conversation_id,
                    request_id="finalize-fence", status="interrupted",
                    lease_token="active-worker",
                ))
                self.assertTrue(self.store.finish_generation(
                    owner_id=self.owner, conversation_id=conversation_id,
                    request_id="finalize-fence", status="interrupted",
                    lease_token="replacement-worker",
                ))

    def test_recovery_cannot_mark_tool_uncertain_after_lease_takeover(self):
        conversation_id = self.conversation["conversation_id"]
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id,
            role="user", content="run tool", idempotency_key="user:recovery-fence",
            expected_version=0, generation_request_id="recovery-fence",
        )
        with patch("core.conversation_store.time.time", return_value=5_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="original-worker", ttl_seconds=5,
            )
            self.store.begin_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id="recovery-fence", call_id="tool-1", tool_name="send",
                arguments_sha256="a" * 64, lease_token="original-worker",
                idempotency_key="b" * 64,
            )
            with patch("core.conversation_store.time.time", return_value=5_006.0):
                self.store.acquire_generation_lease(
                    owner_id=self.owner, conversation_id=conversation_id,
                    lease_token="recovery-worker", ttl_seconds=30,
                )
                self.assertEqual(self.store.mark_started_tool_executions_uncertain(
                    owner_id=self.owner, conversation_id=conversation_id,
                    request_id="recovery-fence", lease_token="original-worker",
                ), 0)
                self.assertEqual(self.store.list_tool_executions(
                    owner_id=self.owner, conversation_id=conversation_id,
                    request_id="recovery-fence",
                )[0]["status"], "started")
                self.assertEqual(self.store.mark_started_tool_executions_uncertain(
                    owner_id=self.owner, conversation_id=conversation_id,
                    request_id="recovery-fence", lease_token="recovery-worker",
                ), 1)
                self.assertEqual(self.store.list_tool_executions(
                    owner_id=self.owner, conversation_id=conversation_id,
                    request_id="recovery-fence",
                )[0]["status"], "uncertain")

    def test_assistant_message_commit_requires_active_matching_lease(self):
        conversation_id = self.conversation["conversation_id"]
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id,
            role="user", content="hello", idempotency_key="user:lease-test",
            expected_version=0, generation_request_id="lease-test",
        )
        with patch("core.conversation_store.time.time", return_value=3_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="current-worker", ttl_seconds=5,
            )
            with self.assertRaisesRegex(ConversationConflict, "lease expired or is no longer owned"):
                self.store.append_message(
                    owner_id=self.owner, conversation_id=conversation_id,
                    role="assistant", content="stale", idempotency_key="assistant:stale",
                    expected_version=1, lease_token="stale-worker",
                )
            accepted = self.store.append_message(
                owner_id=self.owner, conversation_id=conversation_id,
                role="assistant", content="valid", idempotency_key="assistant:valid",
                expected_version=1, lease_token="current-worker",
            )
            self.assertEqual(accepted["message"]["content"], "valid")

    def test_assistant_commit_rejects_expired_matching_lease_using_epoch_clock(self):
        conversation_id = self.conversation["conversation_id"]
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id,
            role="user", content="hello", idempotency_key="user:expired-lease",
            expected_version=0, generation_request_id="expired-lease",
        )
        with patch("core.conversation_store.time.time", return_value=4_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="expiring-worker", ttl_seconds=5,
            )
        with patch("core.conversation_store.time.time", return_value=4_006.0):
            with self.assertRaisesRegex(ConversationConflict, "lease expired or is no longer owned"):
                self.store.append_message(
                    owner_id=self.owner, conversation_id=conversation_id,
                    role="assistant", content="must not persist",
                    idempotency_key="assistant:expired-lease",
                    expected_version=1, lease_token="expiring-worker",
                )

    def test_lease_renewal_extends_only_the_live_matching_fencing_token(self):
        conversation_id = self.conversation["conversation_id"]
        with patch("core.conversation_store.time.time", return_value=2_000.0):
            initial = self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="renewable-worker", ttl_seconds=10,
            )
        with patch("core.conversation_store.time.time", return_value=2_005.0):
            renewed = self.store.renew_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="renewable-worker", ttl_seconds=30,
            )
            self.assertEqual(renewed["expires_at"], 2_035.0)
            with self.assertRaises(ConversationConflict):
                self.store.renew_generation_lease(
                    owner_id=self.owner, conversation_id=conversation_id,
                    lease_token="stale-worker", ttl_seconds=30,
                )
        self.assertGreater(renewed["expires_at"], initial["expires_at"])
        with patch("core.conversation_store.time.time", return_value=2_036.0):
            with self.assertRaisesRegex(ConversationConflict, "expired or is no longer owned"):
                self.store.renew_generation_lease(
                    owner_id=self.owner, conversation_id=conversation_id,
                    lease_token="renewable-worker", ttl_seconds=30,
                )

    def test_create_append_and_list_preserve_sequence_and_version(self):
        user = self.append("user", "Hello Yuki", "user-1", 0)
        assistant = self.append("assistant", "Hello. How can I help?", "assistant-1", 1)
        self.assertEqual(user["version"], 1)
        self.assertEqual(assistant["version"], 2)
        messages = self.store.list_messages(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual([m["sequence"] for m in messages], [1, 2])

    def test_owner_scope_hides_conversation_and_messages(self):
        conversation_id = self.conversation["conversation_id"]
        with self.assertRaises(ConversationNotFound):
            self.store.get_conversation(owner_id=self.other_owner, conversation_id=conversation_id)
        with self.assertRaises(ConversationNotFound):
            self.store.list_messages(owner_id=self.other_owner, conversation_id=conversation_id)
        with self.assertRaises(ConversationNotFound):
            self.store.append_message(
                owner_id=self.other_owner, conversation_id=conversation_id,
                role="user", content="intrude", idempotency_key="x",
            )

    def test_optimistic_version_rejects_stale_writer(self):
        self.append("user", "First", "first", 0)
        with self.assertRaises(ConversationConflict):
            self.append("user", "Stale", "stale", 0)
        self.assertEqual(len(self.store.list_messages(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        )), 1)

    def test_idempotent_replay_returns_existing_message(self):
        first = self.append("user", "Retry safely", "request-1", 0)
        replay = self.append("user", "Retry safely", "request-1", 0)
        self.assertTrue(first["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(first["message"]["message_id"], replay["message"]["message_id"])
        with self.assertRaises(ConversationConflict):
            self.append("user", "Different payload", "request-1", 1)

    def test_conversation_history_is_paginated(self):
        for i in range(5):
            self.append("user", f"message {i}", f"key-{i}", i)
        latest = self.store.list_messages(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"], limit=2
        )
        after = self.store.list_messages(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            after_sequence=3, limit=2,
        )
        self.assertEqual([m["sequence"] for m in latest], [4, 5])
        self.assertEqual([m["sequence"] for m in after], [4, 5])

    def test_archive_hides_from_default_list_and_prevents_writes(self):
        self.store.archive_conversation(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual(self.store.list_conversations(owner_id=self.owner), [])
        self.assertEqual(len(self.store.list_conversations(
            owner_id=self.owner, include_archived=True
        )), 1)
        with self.assertRaises(ConversationConflict):
            self.append("user", "No longer writable", "after-archive", 0)

    def test_rejects_invalid_roles_content_and_versions(self):
        conversation_id = self.conversation["conversation_id"]
        for role, content, version in (
            ([], "x", 0), ("system", "x", 0), ("user", "", 0),
            ("user", "x", True), ("assistant", "x" * 24_001, 0),
        ):
            with self.subTest(role=role, version=version), self.assertRaises(ValueError):
                self.store.append_message(
                    owner_id=self.owner, conversation_id=conversation_id,
                    role=role, content=content, idempotency_key="invalid-" + str(version),
                    expected_version=version,
                )

    def test_title_is_derived_from_first_user_message(self):
        result = self.append("user", "  A concise   title  ", "title", 0)
        conversation = self.store.get_conversation(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual(conversation["title"], "A concise title")

    def test_list_conversations_is_owner_scoped_and_limited(self):
        self.store.create_conversation(owner_id=self.other_owner, title="Private")
        self.assertEqual(len(self.store.list_conversations(owner_id=self.owner)), 1)
        with self.assertRaises(ValueError):
            self.store.list_conversations(owner_id=self.owner, limit=101)




    def test_tool_execution_journal_claims_before_side_effect_and_rejects_replay(self):
        conversation_id = self.conversation["conversation_id"]
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id, role="user",
            content="Execute tool", idempotency_key="tool-journal-request",
            expected_version=0, generation_request_id="tool-journal-request",
        )
        with patch("core.conversation_store.time.time", return_value=10_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="journal-worker", ttl_seconds=60,
            )
            digest = "a" * 64
            first = self.store.begin_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id="tool-journal-request", call_id="call-1",
                tool_name="send_email", arguments_sha256=digest,
                lease_token="journal-worker",
            )
            self.assertTrue(first["created"])
            replay = self.store.begin_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id="tool-journal-request", call_id="call-1",
                tool_name="send_email", arguments_sha256=digest,
                lease_token="journal-worker",
            )
            self.assertFalse(replay["created"])
            self.assertEqual(replay["status"], "started")
            with self.assertRaises(ConversationConflict):
                self.store.begin_tool_execution(
                    owner_id=self.owner, conversation_id=conversation_id,
                    request_id="tool-journal-request", call_id="call-1",
                    tool_name="send_email", arguments_sha256="b" * 64,
                    lease_token="journal-worker",
                )
            self.assertTrue(self.store.finish_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id="tool-journal-request", call_id="call-1",
                lease_token="journal-worker", status="completed",
            ))
            self.assertFalse(self.store.finish_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id="tool-journal-request", call_id="call-1",
                lease_token="journal-worker", status="failed",
            ))
        records = self.store.list_tool_executions(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id="tool-journal-request",
        )
        self.assertEqual(records[0]["status"], "completed")

    def test_crash_recovery_marks_started_tool_execution_uncertain(self):
        conversation_id = self.conversation["conversation_id"]
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id, role="user",
            content="Possibly send email", idempotency_key="tool-uncertain-request",
            expected_version=0, generation_request_id="tool-uncertain-request",
        )
        with patch("core.conversation_store.time.time", return_value=20_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="uncertain-worker", ttl_seconds=60,
            )
            self.store.begin_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id="tool-uncertain-request", call_id="call-uncertain",
                tool_name="send_email", arguments_sha256="c" * 64,
                lease_token="uncertain-worker",
            )
        changed = self.store.mark_started_tool_executions_uncertain(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id="tool-uncertain-request",
        )
        self.assertEqual(changed, 1)
        records = self.store.list_tool_executions(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id="tool-uncertain-request",
        )
        self.assertEqual(records[0]["status"], "uncertain")

    def test_user_message_and_generation_run_are_created_atomically(self):
        result = self.store.append_message(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            role="user", content="Recover me", idempotency_key="recover-1",
            expected_version=0, generation_request_id="recover-1",
        )
        self.assertTrue(result["created"])
        pending = self.store.list_interrupted_generations(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["request_id"], "recover-1")
        self.assertEqual(pending[0]["status"], "running")

    def test_generation_checkpoint_is_bounded_and_terminal_state_is_immutable(self):
        self.store.append_message(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            role="user", content="Checkpoint me", idempotency_key="checkpoint-1",
            expected_version=0, generation_request_id="checkpoint-1",
        )
        self.store.acquire_generation_lease(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            lease_token="checkpoint-worker", ttl_seconds=30,
        )
        self.assertTrue(self.store.update_generation_checkpoint(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            request_id="checkpoint-1", checkpoint={"phase": "model_running", "turn": 1},
            lease_token="checkpoint-worker",
        ))
        self.assertTrue(self.store.finish_generation(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            request_id="checkpoint-1", status="interrupted",
        ))
        self.assertFalse(self.store.finish_generation(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            request_id="checkpoint-1", status="completed",
        ))
        self.assertEqual(self.store.list_interrupted_generations(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        ), [])
        with self.assertRaises(ValueError):
            self.store.update_generation_checkpoint(
                owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
                request_id="checkpoint-1", checkpoint={"payload": "x" * 5000},
            )

    def test_generation_records_are_owner_scoped(self):
        self.store.append_message(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            role="user", content="Private generation", idempotency_key="private-1",
            expected_version=0, generation_request_id="private-1",
        )
        self.assertEqual(self.store.list_interrupted_generations(
            owner_id=self.other_owner, conversation_id=self.conversation["conversation_id"]
        ), [])


    def test_uncertain_tool_review_is_owner_scoped_append_only_and_does_not_resolve_outcome(self):
        conversation_id = self.conversation["conversation_id"]
        request_id = "review-uncertain-request"
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id, role="user",
            content="Review uncertain action", idempotency_key=request_id,
            expected_version=0, generation_request_id=request_id,
        )
        with patch("core.conversation_store.time.time", return_value=50_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="review-worker", ttl_seconds=60,
            )
            self.store.begin_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-review", tool_name="send_email",
                arguments_sha256="a" * 64, lease_token="review-worker",
            )
            self.assertTrue(self.store.finish_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-review",
                lease_token="review-worker", status="uncertain",
            ))
        with self.assertRaises(ConversationNotFound):
            self.store.review_tool_execution(
                owner_id=self.other_owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-review",
                action="acknowledge_uncertain", reason="Not my execution",
            )
        with self.assertRaises(ConversationConflict):
            self.store.review_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-review",
                action="retry", reason="Do it again",
            )
        first = self.store.review_tool_execution(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id=request_id, call_id="call-review",
            action="acknowledge_uncertain", reason="Checked provider dashboard; outcome remains unknown.",
        )
        second = self.store.review_tool_execution(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id=request_id, call_id="call-review",
            action="acknowledge_uncertain", reason="Escalated to operations.",
        )
        self.assertNotEqual(first["review_id"], second["review_id"])
        self.assertEqual(len(self.store.list_tool_execution_reviews(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id=request_id, call_id="call-review",
        )), 2)
        record = self.store.list_tool_executions(
            owner_id=self.owner, conversation_id=conversation_id, request_id=request_id,
        )[0]
        self.assertEqual(record["status"], "uncertain")

    def test_tool_execution_persists_provider_idempotency_contract(self):
        conversation_id = self.conversation["conversation_id"]
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id, role="user",
            content="idempotency", idempotency_key="provider-key-request",
            expected_version=0, generation_request_id="provider-key-request",
        )
        with patch("core.conversation_store.time.time", return_value=30_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="provider-key-worker", ttl_seconds=60,
            )
            record = self.store.begin_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id="provider-key-request", call_id="call-provider",
                tool_name="send_email", arguments_sha256="e" * 64,
                idempotency_key="f" * 64, provider_idempotency=True,
                lease_token="provider-key-worker",
            )
            self.assertTrue(record["provider_idempotency"])
            self.assertEqual(record["idempotency_key"], "f" * 64)
        listed = self.store.list_tool_executions(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id="provider-key-request",
        )
        self.assertTrue(listed[0]["provider_idempotency"])
        self.assertEqual(listed[0]["idempotency_key"], "f" * 64)


    def test_provider_outcome_evidence_is_owner_scoped_append_only_and_never_resolves_execution(self):
        from core.provider_outcome import ProviderOutcome, ProviderOutcomeEvidence
        conversation_id = self.conversation["conversation_id"]
        request_id = "provider-evidence-request"
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id, role="user",
            content="Check provider outcome", idempotency_key=request_id,
            expected_version=0, generation_request_id=request_id,
        )
        with patch("core.conversation_store.time.time", return_value=70_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="evidence-worker", ttl_seconds=60,
            )
            self.store.begin_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-evidence", tool_name="send_email",
                arguments_sha256="b" * 64, idempotency_key="a" * 64,
                lease_token="evidence-worker",
            )
            self.store.finish_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-evidence",
                lease_token="evidence-worker", status="uncertain",
            )
        evidence = ProviderOutcomeEvidence(
            provider="mail.example", outcome=ProviderOutcome.UNKNOWN,
            idempotency_key="a" * 64, arguments_sha256="b" * 64,
            evidence_sha256="c" * 64,
            observed_at="2026-11-14T10:00:00+00:00",
            provider_reference="message-reference-1",
        )
        saved = self._record_provider_evidence(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id=request_id, call_id="call-evidence", evidence=evidence,
        )
        self.assertEqual(saved["outcome"], "unknown")
        repeated = self._record_provider_evidence(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id=request_id, call_id="call-evidence", evidence=evidence,
        )
        self.assertEqual(repeated["evidence_id"], saved["evidence_id"])
        self.assertEqual(len(self.store.list_provider_outcome_evidence(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id=request_id, call_id="call-evidence",
        )), 1)
        self.assertEqual(self.store.list_provider_outcome_evidence(
            owner_id=self.other_owner, conversation_id=conversation_id,
            request_id=request_id, call_id="call-evidence",
        ), [])
        record = self.store.list_tool_executions(
            owner_id=self.owner, conversation_id=conversation_id, request_id=request_id,
        )[0]
        self.assertEqual(record["status"], "uncertain")
        mismatched = ProviderOutcomeEvidence(
            provider="mail.example", outcome=ProviderOutcome.UNKNOWN,
            idempotency_key="d" * 64, arguments_sha256="b" * 64,
            evidence_sha256="e" * 64,
            observed_at="2026-11-14T10:00:00+00:00",
        )
        with self.assertRaises(ConversationConflict):
            self._record_provider_evidence(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-evidence", evidence=mismatched,
            )


    def test_provider_evidence_digest_cannot_be_reused_with_different_reference(self):
        from core.provider_outcome import ProviderOutcome, ProviderOutcomeEvidence
        conversation_id = self.conversation["conversation_id"]
        request_id = "provider-reference-collision"
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id, role="user",
            content="Check provider reference", idempotency_key=request_id,
            expected_version=0, generation_request_id=request_id,
        )
        with patch("core.conversation_store.time.time", return_value=80_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="reference-worker", ttl_seconds=60,
            )
            self.store.begin_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-reference", tool_name="send_email",
                arguments_sha256="b" * 64, idempotency_key="a" * 64,
                lease_token="reference-worker",
            )
            self.store.finish_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-reference",
                lease_token="reference-worker", status="uncertain",
            )
        common = dict(
            provider="mail.example", outcome=ProviderOutcome.UNKNOWN,
            idempotency_key="a" * 64, arguments_sha256="b" * 64,
            evidence_sha256="c" * 64, observed_at="2026-11-14T10:00:00+00:00",
        )
        first = ProviderOutcomeEvidence(**common, provider_reference="message-A")
        conflicting = ProviderOutcomeEvidence(**common, provider_reference="message-B")
        saved = self._record_provider_evidence(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id=request_id, call_id="call-reference", evidence=first,
        )
        with self.assertRaises(ConversationConflict):
            self._record_provider_evidence(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-reference", evidence=conflicting,
            )
        self.assertEqual(len(self.store.list_provider_outcome_evidence(
            owner_id=self.owner, conversation_id=conversation_id,
            request_id=request_id, call_id="call-reference",
        )), 1)

    def test_provider_reconciliation_lease_renewal_is_token_fenced(self):
        conversation_id = self.conversation["conversation_id"]
        request_id = "provider-renew-request"
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id, role="user",
            content="Renew reconciliation", idempotency_key=request_id,
            expected_version=0, generation_request_id=request_id,
        )
        with patch("core.conversation_store.time.time", return_value=100_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="generation-renew", ttl_seconds=60,
            )
            self.store.begin_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-renew", tool_name="send_email",
                arguments_sha256="b" * 64, idempotency_key="a" * 64,
                lease_token="generation-renew",
            )
            self.store.finish_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-renew",
                lease_token="generation-renew", status="uncertain",
            )
        with patch("core.conversation_store.time.time", return_value=100_001.0):
            claim = self.store.acquire_provider_reconciliation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-renew", lease_token="renew-worker",
                ttl_seconds=5,
            )
            renewed = self.store.renew_provider_reconciliation_lease(
                owner_id=self.owner, execution_id=claim["execution_id"],
                lease_token="renew-worker", ttl_seconds=30,
            )
            self.assertEqual(renewed["expires_at"], 100_031.0)
            with self.assertRaises(ConversationConflict):
                self.store.renew_provider_reconciliation_lease(
                    owner_id=self.owner, execution_id=claim["execution_id"],
                    lease_token="stale-worker", ttl_seconds=30,
                )
        with patch("core.conversation_store.time.time", return_value=100_032.0):
            with self.assertRaises(ConversationConflict):
                self.store.renew_provider_reconciliation_lease(
                    owner_id=self.owner, execution_id=claim["execution_id"],
                    lease_token="renew-worker", ttl_seconds=30,
                )

    def test_provider_reconciliation_claim_is_exclusive_and_fences_stale_workers(self):
        conversation_id = self.conversation["conversation_id"]
        request_id = "provider-claim-request"
        self.store.append_message(
            owner_id=self.owner, conversation_id=conversation_id, role="user",
            content="Reconcile once", idempotency_key=request_id,
            expected_version=0, generation_request_id=request_id,
        )
        with patch("core.conversation_store.time.time", return_value=90_000.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="generation-worker", ttl_seconds=60,
            )
            self.store.begin_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-claim", tool_name="send_email",
                arguments_sha256="b" * 64, idempotency_key="a" * 64,
                lease_token="generation-worker",
            )
            self.store.finish_tool_execution(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-claim",
                lease_token="generation-worker", status="uncertain",
            )
        with patch("core.conversation_store.time.time", return_value=91_000.0):
            self.store.acquire_provider_reconciliation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-claim",
                lease_token="worker-old", ttl_seconds=5,
            )
            with self.assertRaises(ConversationConflict):
                self.store.acquire_provider_reconciliation_lease(
                    owner_id=self.owner, conversation_id=conversation_id,
                    request_id=request_id, call_id="call-claim",
                    lease_token="worker-racing",
                )
        with patch("core.conversation_store.time.time", return_value=91_006.0):
            self.store.acquire_provider_reconciliation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-claim",
                lease_token="worker-new", ttl_seconds=30,
            )
            evidence = self._make_claim_test_evidence()
            with self.assertRaisesRegex(ConversationConflict, "claim expired"):
                self.store.record_provider_outcome_evidence(
                    owner_id=self.owner, conversation_id=conversation_id,
                    request_id=request_id, call_id="call-claim", evidence=evidence,
                    reconciliation_lease_token="worker-old",
                )
            saved = self.store.record_provider_outcome_evidence(
                owner_id=self.owner, conversation_id=conversation_id,
                request_id=request_id, call_id="call-claim", evidence=evidence,
                reconciliation_lease_token="worker-new",
            )
            self.assertEqual(saved["outcome"], "unknown")
            self.assertFalse(self.store.release_provider_reconciliation_lease(
                owner_id=self.owner, execution_id=saved["execution_id"],
                lease_token="worker-old",
            ))
            self.assertTrue(self.store.release_provider_reconciliation_lease(
                owner_id=self.owner, execution_id=saved["execution_id"],
                lease_token="worker-new",
            ))

    def _make_claim_test_evidence(self):
        from core.provider_outcome import ProviderOutcome, ProviderOutcomeEvidence
        return ProviderOutcomeEvidence(
            provider="mail.example", outcome=ProviderOutcome.UNKNOWN,
            idempotency_key="a" * 64, arguments_sha256="b" * 64,
            evidence_sha256="f" * 64, observed_at="2026-11-14T10:00:00+00:00",
        )

if __name__ == "__main__":
    unittest.main()
