import json
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import yuki_learning
import yuki_tasks
import yuki_security
from providers import MockProvider
from yuki_agent import YukiAgent
from yuki_tools import TOOLS, calculate, run_tool
from yuki_security import SecurityPolicy, sanitize_text, write_audit_event
from yuki_providers import ProviderInfo, ProviderManager
from yuki import handle_command
from yuki_planner import create_plan, plan_summary
from yuki_events import EventBus
from yuki_approvals import ApprovalGate



class PlannerTests(unittest.TestCase):
    def test_plan_is_bounded_and_does_not_execute(self):
        plan = create_plan("Review repository", ["Inspect files", "Run tests"])
        summary = plan_summary(plan)
        self.assertIn("Plan is a proposal only", summary)
        self.assertEqual([step.status for step in plan.steps], ["pending", "pending"])

    def test_plan_rejects_too_many_steps(self):
        with self.assertRaises(ValueError):
            create_plan("Too large", [f"step {i}" for i in range(11)])


class EventBusTests(unittest.TestCase):
    def test_subscribe_publish_and_unsubscribe(self):
        bus = EventBus()
        received = []
        unsubscribe = bus.subscribe("task.started", received.append)
        self.assertEqual(bus.publish("task.started", {"id": "abc"}), 1)
        self.assertEqual(received, [{"id": "abc"}])
        unsubscribe()
        self.assertEqual(bus.publish("task.started", {"id": "def"}), 0)


class ApprovalTests(unittest.TestCase):
    def test_approval_gate_records_decision_without_execution(self):
        gate = ApprovalGate()
        request = gate.request("Publish release", "User requested a release")
        self.assertEqual(request.status, "pending")
        self.assertEqual(gate.decide(request.id, True),
                         f"Request {request.id} approved. No action was executed.")
        self.assertEqual(gate.decide(request.id, False), "Request already approved.")


class ProviderTests(unittest.TestCase):
    def test_mock_provider_echoes_prompt(self):
        self.assertIn("hello", MockProvider().generate("hello"))

    def test_agent_rejects_empty_prompt(self):
        self.assertEqual(YukiAgent().ask("   "), "Yuki: Please enter a message.")

    def test_agent_handles_provider_exception(self):
        class BrokenProvider:
            def is_available(self):
                raise RuntimeError("private provider details")
            def generate(self, prompt):
                raise AssertionError("should not be called")
        self.assertEqual(YukiAgent(BrokenProvider()).ask("hello"),
                         "Yuki: The provider encountered an error.")


class ProviderManagerTests(unittest.TestCase):
    def test_only_implemented_provider_can_be_selected(self):
        manager = ProviderManager()
        self.assertEqual(manager.select("mock"), "Selected provider: mock")
        result = manager.select("openai")
        self.assertIn("not implemented", result)
        self.assertEqual(manager.current().name, "mock")

    def test_selected_adapter_is_created(self):
        manager = ProviderManager()
        adapter = manager.create_selected_adapter()
        self.assertIsInstance(adapter, MockProvider)

    def test_provider_selection_constructs_adapter_once_and_commits_atomically(self):
        manager = ProviderManager()
        created = []

        class FlakyProvider(MockProvider):
            pass

        def factory():
            if created:
                raise RuntimeError("factory must not be called twice")
            adapter = FlakyProvider()
            created.append(adapter)
            return adapter

        manager.register(ProviderInfo("flaky", ("test",), True), factory=factory)
        agent = YukiAgent(provider=manager.create_selected_adapter())
        result = handle_command("/provider select flaky", [], [], agent, manager,
                                SecurityPolicy(allowed_tools=TOOLS.keys()))

        self.assertEqual(result, "Selected provider: flaky")
        self.assertEqual(manager.current().name, "flaky")
        self.assertIs(agent.provider, created[0])
        self.assertIs(manager.create_selected_adapter(), created[0])

    def test_terminal_provider_selection_does_not_claim_unimplemented_provider(self):
        manager = ProviderManager()
        agent = YukiAgent(provider=manager.create_selected_adapter())
        result = handle_command("/provider select openai", [], [], agent, manager,
                                SecurityPolicy(allowed_tools=TOOLS.keys()))
        self.assertIn("not implemented", result)
        self.assertIsInstance(agent.provider, MockProvider)

    def test_dashboard_reports_core_status(self):
        manager = ProviderManager()
        agent = YukiAgent(provider=manager.create_selected_adapter())
        result = handle_command("/dashboard", [], [], agent, manager,
                                SecurityPolicy(allowed_tools=TOOLS.keys()))
        self.assertIn("Yuki dashboard", result)
        self.assertIn("Provider: mock", result)


class ToolTests(unittest.TestCase):
    def test_calculator_handles_arithmetic(self):
        self.assertEqual(calculate("12 * (3 + 4)"), "84")

    def test_calculator_rejects_code(self):
        self.assertIn("Could not calculate", calculate("__import__('os').system('echo unsafe')"))

    def test_calculator_rejects_boolean(self):
        self.assertIn("Could not calculate", calculate("True + 1"))

    def test_unknown_tool_is_rejected(self):
        self.assertIn("Unknown tool", run_tool("shell", "whoami"))


class SecurityTests(unittest.TestCase):
    def test_sanitizer_redacts_credentials(self):
        self.assertNotIn("supersecretvalue123", sanitize_text("api_key=supersecretvalue123"))
        self.assertIn("[REDACTED]", sanitize_text("api_key=supersecretvalue123"))

    def test_empty_policy_denies_every_tool(self):
        policy = SecurityPolicy(allowed_tools=[])
        self.assertEqual(policy.allowed_tools(), [])
        self.assertFalse(policy.authorize_tool("calculate"))

    def test_policy_defaults_to_allow_list(self):
        policy = SecurityPolicy(allowed_tools=TOOLS.keys())
        self.assertTrue(policy.authorize_tool("calculate"))
        self.assertFalse(policy.authorize_tool("shell"))
        self.assertFalse(policy.authorize_tool("../calculate"))

    def test_audit_log_stores_metadata_only_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "audit.json"
            with patch.object(yuki_security, "AUDIT_FILE", path):
                self.assertTrue(write_audit_event("tool:calculate", "completed"))
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data[0]["action"], "tool:calculate")
                self.assertNotIn("argument", data[0])
                self.assertNotIn("secret", data[0])


class TaskTests(unittest.TestCase):
    def _patch_task_paths(self, folder):
        path = Path(folder) / "tasks.json"
        db_path = Path(folder) / "tasks.sqlite3"
        return path, db_path

    def test_tasks_persist_and_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            path, db_path = self._patch_task_paths(folder)
            with patch.object(yuki_tasks, "TASKS_FILE", path), \
                 patch.object(yuki_tasks, "TASKS_DB_FILE", db_path):
                tasks = []
                self.assertEqual(yuki_tasks.add_task(tasks, "Test task"), "Task added.")
                self.assertEqual(yuki_tasks.complete_task(tasks, 1), "Task completed.")
                self.assertTrue(yuki_tasks.load_tasks()[0]["completed"])
                self.assertTrue(db_path.exists())

    def test_valid_legacy_json_is_migrated_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path, db_path = self._patch_task_paths(folder)
            legacy = [{"id": 7, "description": "old task", "completed": False}]
            path.write_text(json.dumps(legacy), encoding="utf-8")
            with patch.object(yuki_tasks, "TASKS_FILE", path), \
                 patch.object(yuki_tasks, "TASKS_DB_FILE", db_path):
                self.assertEqual(yuki_tasks.load_tasks(), legacy)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), legacy)
                self.assertEqual(yuki_tasks.load_tasks(), legacy)

    def test_corrupt_task_file_returns_empty_list_and_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path, db_path = self._patch_task_paths(folder)
            path.write_text("{broken", encoding="utf-8")
            with patch.object(yuki_tasks, "TASKS_FILE", path), \
                 patch.object(yuki_tasks, "TASKS_DB_FILE", db_path):
                self.assertEqual(yuki_tasks.load_tasks(), [])
                self.assertEqual(path.read_text(encoding="utf-8"), "{broken")
                self.assertFalse(db_path.exists())

    def test_concurrent_task_additions_allocate_unique_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path, db_path = self._patch_task_paths(folder)
            with patch.object(yuki_tasks, "TASKS_FILE", path), \
                 patch.object(yuki_tasks, "TASKS_DB_FILE", db_path):
                def add(index):
                    local_tasks = yuki_tasks.load_tasks()
                    return yuki_tasks.add_task(local_tasks, f"task-{index}")

                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(add, range(40)))
                self.assertTrue(all(result == "Task added." for result in results))
                stored = yuki_tasks.load_tasks()
                self.assertEqual(len(stored), 40)
                self.assertEqual(len({item["id"] for item in stored}), 40)


class LearningTests(unittest.TestCase):
    def test_outcome_is_saved_and_matching_lesson_found(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning.json"
            with patch.object(yuki_learning, "HISTORY_FILE", path), \
                 patch.object(yuki_learning, "HISTORY_DB_FILE", Path(folder) / "learning.sqlite3"):
                result = yuki_learning.record_outcome(
                    "Organize project files", "success",
                    "Group files by type", "Group related files before reviewing them"
                )
                self.assertIn("recorded", result.lower())
                self.assertTrue(yuki_learning.suggest_lessons("Organize project files"))

    def test_concurrent_outcomes_are_not_lost(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning.json"
            db_path = Path(folder) / "learning.sqlite3"
            with patch.object(yuki_learning, "HISTORY_FILE", path), \
                 patch.object(yuki_learning, "HISTORY_DB_FILE", db_path):
                def record(index):
                    return yuki_learning.record_outcome(
                        f"task-{index}", "success", lesson=f"lesson-{index}"
                    )
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(record, range(40)))
                self.assertTrue(all("recorded" in result.lower() for result in results))
                history = yuki_learning.load_history()
                self.assertEqual(len(history), 40)
                self.assertEqual({item["task"] for item in history},
                                 {f"task-{index}" for index in range(40)})

    def test_valid_legacy_learning_history_is_migrated_and_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning.json"
            db_path = Path(folder) / "learning.sqlite3"
            legacy = [{"task": "old task", "outcome": "ok", "approach": "simple",
                       "lesson": "keep backups", "created_at": "2026-01-01T00:00:00Z"}]
            path.write_text(json.dumps(legacy), encoding="utf-8")
            with patch.object(yuki_learning, "HISTORY_FILE", path), \
                 patch.object(yuki_learning, "HISTORY_DB_FILE", db_path):
                self.assertEqual(yuki_learning.load_history(), legacy)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), legacy)
                self.assertTrue(db_path.exists())

    def test_malformed_legacy_learning_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning.json"
            path.write_text("{broken", encoding="utf-8")
            with patch.object(yuki_learning, "HISTORY_FILE", path), \
                 patch.object(yuki_learning, "HISTORY_DB_FILE", Path(folder) / "learning.sqlite3"):
                self.assertEqual(yuki_learning.load_history(), [])
                self.assertEqual(path.read_text(encoding="utf-8"), "{broken")
                self.assertFalse((Path(folder) / "learning.sqlite3").exists())

    def test_learning_redacts_key_like_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning.json"
            with patch.object(yuki_learning, "HISTORY_FILE", path), \
                 patch.object(yuki_learning, "HISTORY_DB_FILE", Path(folder) / "learning.sqlite3"):
                yuki_learning.record_outcome("test", "api_key=supersecretvalue123")
                saved = json.dumps(yuki_learning.load_history(), ensure_ascii=False)
                self.assertNotIn("supersecretvalue123", saved)
                self.assertIn("[REDACTED]", saved)


    def test_dashboard_uses_real_line_breaks(self):
        manager = ProviderManager()
        agent = YukiAgent(provider=manager.create_selected_adapter())
        result = handle_command("/dashboard", [], [], agent, manager,
                                SecurityPolicy(allowed_tools=TOOLS.keys()))
        self.assertIn("\nProvider: mock", result)
        self.assertNotIn("\\nProvider: mock", result)

    def test_skill_list_uses_real_line_breaks(self):
        manager = ProviderManager()
        agent = YukiAgent(provider=manager.create_selected_adapter())
        with patch("yuki.SkillRegistry.defaults") as defaults:
            registry = defaults.return_value
            registry.describe.return_value = [
                {"name": "first", "risk": "low", "description": "first skill"},
                {"name": "second", "risk": "low", "description": "second skill"},
            ]
            result = handle_command("/skill list", [], [], agent, manager,
                                    SecurityPolicy(allowed_tools=TOOLS.keys()))
        self.assertIn("\nsecond [low]", result)
        self.assertNotIn("\\nsecond [low]", result)



if __name__ == "__main__":
    unittest.main()
