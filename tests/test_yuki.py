import json
import tempfile
import unittest
from pathlib import Path
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



class EvolutionCommandTests(unittest.TestCase):
    def test_evolution_status_command_reports_local_state(self):
        from yuki import handle_command
        from unittest.mock import patch
        with patch("yuki._evolution_cycle") as cycle_factory:
            cycle_factory.return_value.status.return_value = {
                "observations": 3, "pending_proposals": 2, "last_report_at": None
            }
            result = handle_command("/evolve status", [], [], None, None)
        self.assertIn("3 observations", result)
        self.assertIn("2 pending proposals", result)

    def test_evolution_record_command_persists_observation(self):
        from yuki import handle_command
        from unittest.mock import patch
        with patch("yuki._evolution_cycle") as cycle_factory, patch("yuki.write_audit_event"):
            cycle_factory.return_value.record.return_value = {"category": "feedback", "id": "abc123"}
            result = handle_command("/evolve record feedback | prefer concise reports", [], [], None, None)
        self.assertIn("Recorded local feedback observation abc123", result)
        cycle_factory.return_value.record.assert_called_once_with("feedback", "prefer concise reports")

    def test_evolution_review_command_never_claims_to_apply_changes(self):
        from yuki import handle_command
        from unittest.mock import patch
        with patch("yuki._evolution_cycle") as cycle_factory, patch("yuki.write_audit_event"):
            cycle_factory.return_value.review.return_value = {
                "id": "proposal-1", "status": "accepted_for_manual_followup"
            }
            result = handle_command("/evolve review proposal-1 accept", [], [], None, None)
        self.assertIn("no changes were applied", result)
        cycle_factory.return_value.review.assert_called_once_with("proposal-1", "accepted_for_manual_followup")


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
    def test_real_provider_is_default_and_unimplemented_provider_is_rejected(self):
        manager = ProviderManager(environ={})
        self.assertEqual(manager.current().name, "openai")
        self.assertEqual(manager.select("openai"), "Selected provider: openai")
        result = manager.select("anthropic")
        self.assertIn("not implemented", result)
        self.assertEqual(manager.current().name, "openai")

    def test_selected_adapter_is_created(self):
        from providers.openai_compatible import OpenAICompatibleProvider
        manager = ProviderManager(environ={})
        adapter = manager.create_selected_adapter()
        self.assertIsInstance(adapter, OpenAICompatibleProvider)
        self.assertFalse(adapter.is_available())

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
        result = handle_command("/provider select anthropic", [], [], agent, manager,
                                SecurityPolicy(allowed_tools=TOOLS.keys()))
        self.assertIn("not implemented", result)
        from providers.openai_compatible import OpenAICompatibleProvider
        self.assertIsInstance(agent.provider, OpenAICompatibleProvider)

    def test_dashboard_reports_core_status(self):
        manager = ProviderManager()
        agent = YukiAgent(provider=manager.create_selected_adapter())
        result = handle_command("/dashboard", [], [], agent, manager,
                                SecurityPolicy(allowed_tools=TOOLS.keys()))
        self.assertIn("Yuki dashboard", result)
        self.assertIn("Provider: openai", result)


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
    def test_tasks_persist_and_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.json"
            with patch.object(yuki_tasks, "TASKS_FILE", path):
                tasks = []
                self.assertEqual(yuki_tasks.add_task(tasks, "Test task"), "Task added.")
                self.assertEqual(yuki_tasks.complete_task(tasks, 1), "Task completed.")
                self.assertTrue(yuki_tasks.load_tasks()[0]["completed"])

    def test_corrupt_task_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.json"
            path.write_text("{broken", encoding="utf-8")
            with patch.object(yuki_tasks, "TASKS_FILE", path):
                self.assertEqual(yuki_tasks.load_tasks(), [])


class LearningTests(unittest.TestCase):
    def test_outcome_is_saved_and_matching_lesson_found(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning.json"
            with patch.object(yuki_learning, "HISTORY_FILE", path):
                result = yuki_learning.record_outcome(
                    "Organize project files", "success",
                    "Group files by type", "Group related files before reviewing them"
                )
                self.assertIn("recorded", result.lower())
                self.assertTrue(yuki_learning.suggest_lessons("Organize project files"))

    def test_learning_redacts_key_like_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "learning.json"
            with patch.object(yuki_learning, "HISTORY_FILE", path):
                yuki_learning.record_outcome("test", "api_key=supersecretvalue123")
                saved = path.read_text(encoding="utf-8")
                self.assertNotIn("supersecretvalue123", saved)
                self.assertIn("[REDACTED]", saved)


    def test_dashboard_uses_real_line_breaks(self):
        manager = ProviderManager()
        agent = YukiAgent(provider=manager.create_selected_adapter())
        result = handle_command("/dashboard", [], [], agent, manager,
                                SecurityPolicy(allowed_tools=TOOLS.keys()))
        self.assertIn("\nProvider: openai", result)
        self.assertNotIn("\\nProvider: openai", result)

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
