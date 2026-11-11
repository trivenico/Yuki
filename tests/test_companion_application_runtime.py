import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from companion.application_runtime import build_companion_application_runtime
from companion.relationship_mode import RelationshipMode, RelationshipModeSettings
from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoTools:
    def authorize_tool(self, name):
        return False


def _loop():
    def model(**kwargs):
        return {"text": "hello", "tool_calls": {"tool_calls": []}}
    return AgentExecutionLoop(model, StructuredToolProtocol(GuardedToolRuntime({}, _NoTools())))


class CompanionApplicationRuntimeTests(unittest.TestCase):
    def test_builds_shared_conversation_and_local_companion_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = build_companion_application_runtime(
                {"workspace": str(root)}, agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            try:
                self.assertTrue(runtime.owner_id.startswith("local-"))
                self.assertIsNotNone(runtime.conversations)
                conversation = runtime.conversations.create(owner_id=runtime.owner_id)
                reply = runtime.conversations.reply(
                    owner_id=runtime.owner_id,
                    conversation_id=conversation["conversation_id"],
                    user_text="Hello Yuki",
                    request_id="companion-runtime-request",
                    expected_version=conversation["version"],
                    tools=[],
                )
                self.assertEqual(reply["answer"], "hello")
                self.assertEqual(runtime.persona.name, "Yuki")
                self.assertEqual(runtime.emotion.tone, "neutral")
                self.assertFalse(runtime.memory_enabled)
                self.assertIsNone(runtime.memory)
                self.assertTrue((root / "companion").is_dir())
                self.assertIn("You are Yuki", runtime.companion_guidance())
                self.assertEqual(runtime.relationship_settings.load(), RelationshipModeSettings())
            finally:
                runtime.close()
                runtime.close()

    def test_relationship_settings_persist_across_runtime_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = build_companion_application_runtime(
                {"workspace": str(root)}, agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            settings = RelationshipModeSettings(
                mode=RelationshipMode.ROMANTIC, adult_confirmed=True,
                topics_to_avoid=("financial stress",),
            )
            first.relationship_settings.save(settings)
            first.close()

            second = build_companion_application_runtime(
                {"workspace": str(root)}, agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            try:
                self.assertEqual(second.relationship_settings.load(), settings)
                # Preference persistence must not silently inject persona guidance.
                self.assertNotIn("romantic", second.companion_guidance().lower())
            finally:
                second.close()

    def test_memory_is_only_constructed_after_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = build_companion_application_runtime(
                {"workspace": str(root), "companion_memory_enabled": True},
                agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            try:
                self.assertTrue(runtime.memory_enabled)
                saved = runtime.memory.add("Owner-approved note", category="note")
                self.assertEqual(runtime.memory.list()[0]["id"], saved["id"])
                self.assertTrue((root / "companion" / "state.sqlite3").is_file())
                self.assertFalse((root / "companion" / "memory.json").exists())
            finally:
                runtime.close()

    def test_reminders_and_identity_are_local_and_consent_gated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = build_companion_application_runtime(
                {"workspace": str(root)}, agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            try:
                with self.assertRaises(PermissionError):
                    runtime.identities.add("Owner", consent=False)
                identity = runtime.identities.add("Owner", consent=True)
                self.assertEqual(runtime.identities.list()[0]["id"], identity["id"])
                reminder = runtime.reminders.add(
                    "Review Yuki", datetime.now(timezone.utc) - timedelta(seconds=1)
                )
                due = runtime.reminders.due()
                self.assertEqual([item["id"] for item in due], [reminder["id"]])
                self.assertEqual(runtime.reminders.due(), [])
            finally:
                runtime.close()

    def test_reminder_notification_outbox_requires_explicit_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = build_companion_application_runtime(
                {"workspace": str(root)}, agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            try:
                reminder = runtime.reminders.add(
                    "Deliver safely", datetime.now(timezone.utc) - timedelta(seconds=1)
                )
                claims = runtime.reminders.claim_due_notifications()
                self.assertEqual(len(claims), 1)
                self.assertEqual(claims[0]["payload"]["id"], reminder["id"])
                self.assertFalse(runtime.reminders.list()[0]["delivered"])
                self.assertTrue(runtime.reminders.complete_notification(claims[0]))
                self.assertTrue(runtime.reminders.list()[0]["delivered"])
                self.assertFalse(runtime.reminders.complete_notification(claims[0]))
            finally:
                runtime.close()

    def test_rejects_invalid_memory_configuration_before_database_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "must be a boolean"):
                build_companion_application_runtime(
                    {"workspace": str(root), "companion_memory_enabled": "yes"},
                    agent_loop=_loop(),
                )
            self.assertFalse((root / "conversations.sqlite3").exists())

    def test_legacy_state_migrates_without_deleting_sources_and_memory_stays_opt_in(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "companion"
            state_dir.mkdir()
            legacy_identity = [{"id": "legacy-owner", "name": "Owner", "consent": True}]
            legacy_reminder = [{
                "id": "legacy-reminder", "text": "Review",
                "due_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                "delivered": False,
            }]
            legacy_memory = [{
                "id": "legacy-memory", "created_at": datetime.now(timezone.utc).isoformat(),
                "category": "note", "text": "private note",
            }]
            (state_dir / "identities.json").write_text(json.dumps(legacy_identity), encoding="utf-8")
            (state_dir / "reminders.json").write_text(json.dumps(legacy_reminder), encoding="utf-8")
            (state_dir / "memory.json").write_text(json.dumps(legacy_memory), encoding="utf-8")
            runtime = build_companion_application_runtime(
                {"workspace": str(root)}, agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            try:
                self.assertEqual(runtime.identities.list()[0]["id"], "legacy-owner")
                self.assertEqual([x["id"] for x in runtime.reminders.due()], ["legacy-reminder"])
                self.assertIsNone(runtime.memory)
                self.assertEqual(json.loads((state_dir / "memory.json").read_text(encoding="utf-8")), legacy_memory)
                self.assertEqual(json.loads((state_dir / "identities.json").read_text(encoding="utf-8")), legacy_identity)
            finally:
                runtime.close()

    def test_legacy_memory_imports_only_after_explicit_opt_in(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "companion"
            state_dir.mkdir()
            legacy = [{
                "id": "legacy-memory", "created_at": datetime.now(timezone.utc).isoformat(),
                "category": "note", "text": "owner-approved",
            }]
            (state_dir / "memory.json").write_text(json.dumps(legacy), encoding="utf-8")
            runtime = build_companion_application_runtime(
                {"workspace": str(root), "companion_memory_enabled": True},
                agent_loop=_loop(),
                store_factory=lambda config: ConversationStore(root / "conversations.sqlite3"),
            )
            try:
                self.assertEqual(runtime.memory.list()[0]["id"], "legacy-memory")
                self.assertEqual(json.loads((state_dir / "memory.json").read_text(encoding="utf-8")), legacy)
            finally:
                runtime.close()

    def test_requires_explicit_workspace(self):
        with self.assertRaisesRegex(ValueError, "workspace"):
            build_companion_application_runtime({}, agent_loop=_loop())


if __name__ == "__main__":
    unittest.main()
