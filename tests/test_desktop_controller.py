import importlib.util
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from companion.application import CompanionApplicationService
from companion.conversation import ConversationReply
from companion.memory_consent import CompanionMemory
from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from core.auth import BuiltInAuthService
from ui.desktop.auth import DesktopSessionManager
from ui.desktop.bootstrap import AuthenticatedDesktopIdentity
from ui.desktop.controller import DesktopCompanionController


class FakeKeyring:
    def __init__(self):
        self.values = {}
    def set_password(self, service, account, value):
        self.values[(service, account)] = value
    def get_password(self, service, account):
        return self.values.get((service, account))
    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class FakeProvider:
    def generate_chat(self, system, messages):
        if "tasks" in system.lower() or "plan" in system.lower():
            return '{"tasks":[{"task_id":"research","description":"Research the requested topic","required_capabilities":["research"],"depends_on":[]}]}'
        return "Yuki reply"


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = BuiltInAuthService(str(Path(self.temp.name) / "auth.sqlite3"), password_iterations=100_000)
        self.auth.bootstrap_admin("desktop.admin", "A strong desktop password 4!")
        self.sessions = DesktopSessionManager(self.auth, FakeKeyring())
        self.identity = self.sessions.login("desktop.admin", "A strong desktop password 4!")
        self.memory = CompanionMemory()
        self.provider = FakeProvider()
        self.runtime = TaskRuntime(
            WorkflowRunStore(str(Path(self.temp.name) / "tasks.sqlite3")),
            authorize=lambda actor, action, resource: actor == self.identity.actor_id,
        )
        self.coordinator = SpecialistAgentCoordinator(
            self.runtime, str(Path(self.temp.name) / "tasks.sqlite3"),
            [SpecialistAgent("research", "Research specialist", frozenset({"research"}), "emit")],
            authorize=lambda actor, action, resource: actor == self.identity.actor_id,
        )
        self.app = CompanionApplicationService(
            self.provider, memory=self.memory,
            agent_coordinator=self.coordinator, task_runtime=self.runtime,
        )
        self.controller = DesktopCompanionController(self.app, self.identity, self.sessions)

    def test_chat_uses_shared_application_service(self):
        self.assertEqual(self.controller.respond("hello"), "Yuki reply")
        self.assertEqual(self.app.session.conversation_turns, 1)

    def test_memory_context_is_explicit_and_revocable(self):
        self.assertFalse(self.controller.memory_context_enabled())
        self.controller.set_memory_context(True)
        self.assertTrue(self.controller.memory_context_enabled())
        self.controller.set_memory_context(False)
        self.assertFalse(self.controller.memory_context_enabled())

    def test_expired_session_blocks_chat_and_memory_changes(self):
        self.auth.revoke_session(self.identity.session_id)
        with self.assertRaises(PermissionError):
            self.controller.respond("hello")
        with self.assertRaises(PermissionError):
            self.controller.set_memory_context(True)
        with self.assertRaises(PermissionError):
            self.controller.memory_context_enabled()

    def test_specialist_catalog_is_exposed_only_for_live_authenticated_actor(self):
        agents = self.controller.list_specialists()
        self.assertEqual(agents[0]["agent_id"], "research")
        self.auth.revoke_session(self.identity.session_id)
        with self.assertRaises(PermissionError):
            self.controller.list_specialists()

    def test_plan_proposal_is_separate_from_dispatch(self):
        plan = self.controller.propose_agent_plan("Research this topic")
        self.assertEqual(len(plan.subtasks), 1)
        self.assertEqual(plan.subtasks[0].status, "pending")
        self.assertIsNone(plan.subtasks[0].task_id)
        inspected = self.controller.get_agent_plan(plan.plan_id)
        self.assertEqual(inspected.plan_id, plan.plan_id)
        dispatched = self.controller.dispatch_agent_plan(plan.plan_id)
        self.assertEqual(dispatched.subtasks[0].status, "queued")
        self.assertIsNotNone(dispatched.subtasks[0].task_id)

    def test_task_listing_uses_authenticated_actor(self):
        plan = self.controller.propose_agent_plan("Research this topic")
        dispatched = self.controller.dispatch_agent_plan(plan.plan_id)
        tasks = self.controller.list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].task_id, dispatched.subtasks[0].task_id)
        updates = self.controller.task_progress(
            tasks[0].task_id, after_sequence=0, limit=50
        )
        self.assertTrue(updates)
        self.assertEqual(updates[0].task_id, tasks[0].task_id)
        self.assertTrue(updates[0].message.startswith("Started task:"))

    def test_task_definition_can_be_reviewed_and_approved_for_exact_version(self):
        task = self.runtime.enqueue(
            actor_id=self.identity.actor_id,
            idempotency_key="desktop-review-test",
            name="reviewable workflow",
            steps=[{
                "name": "inspect",
                "action": "emit",
                "argument": "target=staging; operation=read-only",
                "requires_approval": True,
                "required_permissions": ["workspace.read"],
            }],
        )
        self.assertEqual(task.state, "waiting_approval")
        reviewed = self.controller.get_task(task.task_id)
        self.assertEqual(reviewed.state, "waiting_approval")
        self.assertEqual(reviewed.steps[0]["action"], "emit")
        self.assertEqual(reviewed.steps[0]["argument"], "target=staging; operation=read-only")
        self.assertEqual(reviewed.steps[0]["required_permissions"], ["workspace.read"])
        approved = self.controller.approve_task(
            task.task_id, expected_version=reviewed.version
        )
        self.assertEqual(approved.state, "queued")
        self.assertEqual(approved.version, reviewed.version + 1)

    def test_task_definition_read_is_denied_after_session_expiry(self):
        task = self.runtime.enqueue(
            actor_id=self.identity.actor_id,
            idempotency_key="desktop-review-expiry",
            name="private workflow",
            steps=[{"name": "inspect", "action": "emit", "argument": "private", "requires_approval": True}],
        )
        self.auth.revoke_session(self.identity.session_id)
        with self.assertRaises(PermissionError):
            self.controller.get_task(task.task_id)

    def test_task_progress_is_denied_after_session_expiry(self):
        plan = self.controller.propose_agent_plan("Research this topic")
        dispatched = self.controller.dispatch_agent_plan(plan.plan_id)
        task_id = dispatched.subtasks[0].task_id
        self.auth.revoke_session(self.identity.session_id)
        with self.assertRaises(PermissionError):
            self.controller.task_progress(task_id)


    def test_migration_preview_and_execution_require_live_session(self):
        source = Path(self.temp.name) / "legacy"
        destination = Path(self.temp.name) / "migrated"
        source.mkdir()
        (source / "notes.txt").write_text("legacy")
        preview = self.controller.preview_migration(source, destination)
        self.assertEqual(preview.files, ("notes.txt",))
        result = self.controller.migrate_data(source, destination)
        self.assertEqual(result["files"][0]["path"], "notes.txt")
        self.assertEqual((destination / "notes.txt").read_text(), "legacy")
        self.assertTrue((source / "notes.txt").exists())
        self.auth.revoke_session(self.identity.session_id)
        with self.assertRaises(PermissionError):
            self.controller.preview_migration(source, destination)
        with self.assertRaises(PermissionError):
            self.controller.migrate_data(source, destination)

    def test_migration_rejects_active_profile_and_its_parent(self):
        source = Path(self.temp.name) / "legacy"
        source.mkdir()
        (source / "notes.txt").write_text("legacy")
        active = Path(self.temp.name) / "active-profile"
        active.mkdir()
        with patch("ui.desktop.controller.desktop_data_root", return_value=active):
            with self.assertRaises(ValueError):
                self.controller.preview_migration(source, active)
            with self.assertRaises(ValueError):
                self.controller.migrate_data(source, active.parent)

    def test_migration_rejects_symlink_destination(self):
        source = Path(self.temp.name) / "legacy"
        source.mkdir()
        (source / "notes.txt").write_text("legacy")
        actual = Path(self.temp.name) / "actual"
        actual.mkdir()
        link = Path(self.temp.name) / "linked"
        try:
            link.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ValueError):
            self.controller.preview_migration(source, link)

    @unittest.skipUnless(importlib.util.find_spec("cryptography"), "cryptography optional extra is not installed")
    def test_backup_and_restore_are_bound_to_live_session(self):
        profile = Path(self.temp.name) / "active-profile"
        profile.mkdir()
        (profile / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
        backup = Path(self.temp.name) / "profile.yukibak"
        restored = Path(self.temp.name) / "restored-profile"
        with patch("ui.desktop.controller.desktop_data_root", return_value=profile):
            created = self.controller.create_backup(backup, "a secure backup passphrase")
            self.assertEqual(created["file_count"], 1)
            result = self.controller.restore_backup(
                backup, restored, "a secure backup passphrase"
            )
        self.assertEqual(result["file_count"], 1)
        self.assertEqual((restored / "settings.json").read_text(), '{"theme":"dark"}')
        self.assertTrue((profile / "settings.json").exists())

    def test_restore_refuses_active_profile_and_parent_directory(self):
        profile = Path(self.temp.name) / "active-profile"
        profile.mkdir()
        backup = Path(self.temp.name) / "backup.yukibak"
        with patch("ui.desktop.controller.desktop_data_root", return_value=profile):
            with self.assertRaises(ValueError):
                self.controller.restore_backup(backup, profile, "a secure backup passphrase")
            with self.assertRaises(ValueError):
                self.controller.restore_backup(backup, profile.parent, "a secure backup passphrase")

    def test_restore_rejects_symlinked_destination(self):
        profile = Path(self.temp.name) / "active-profile"
        profile.mkdir()
        destination = Path(self.temp.name) / "linked-restore"
        actual = Path(self.temp.name) / "actual-restore"
        actual.mkdir()
        try:
            destination.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        with patch("ui.desktop.controller.desktop_data_root", return_value=profile):
            with self.assertRaises(ValueError):
                self.controller.restore_backup(
                    Path(self.temp.name) / "backup", destination,
                    "a secure backup passphrase",
                )

    def test_backup_operations_require_live_session(self):
        self.auth.revoke_session(self.identity.session_id)
        with self.assertRaises(PermissionError):
            self.controller.create_backup(Path(self.temp.name) / "backup", "a secure backup passphrase")
        with self.assertRaises(PermissionError):
            self.controller.restore_backup(
                Path(self.temp.name) / "backup", Path(self.temp.name) / "restore",
                "a secure backup passphrase",
            )

    def test_logout_clears_session_conversation_and_memory_context(self):
        self.controller.set_memory_context(True)
        self.controller.respond("hello")
        self.assertTrue(self.controller.logout())
        self.assertFalse(self.memory.context_enabled)
        self.assertEqual(self.app.session.conversation_turns, 0)
        with self.assertRaises(PermissionError):
            self.controller.respond("after logout")


if __name__ == "__main__":
    unittest.main()
