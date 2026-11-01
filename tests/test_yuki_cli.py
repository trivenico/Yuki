import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from core.auth import BuiltInAuthService, task_permission_authorizer
from yuki_cli import run_cli


class FakeProvider:
    def __init__(self, available=True, answer="hello"):
        self.available = available
        self.answer = answer
        self.prompts = []

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.answer

    def generate_chat(self, system, messages):
        self.prompts.append(json.dumps({"system": system, "conversation": messages}))
        return self.answer


class FakeReport:
    def to_dict(self):
        return {"operating_system": "TestOS", "capabilities": {}}


class YukiCliTests(unittest.TestCase):
    def test_ask_uses_configured_provider_and_returns_answer(self):
        provider = FakeProvider(answer="real answer")
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {"provider": "fake"},
            provider_factory=lambda config: provider,
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(output, ["real answer"])
        self.assertEqual(provider.prompts, ["hello"])

    def test_provider_status_reports_unavailable_with_nonzero_exit(self):
        provider = FakeProvider(available=False)
        output = []
        code = run_cli(
            ["provider-status"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        report = json.loads(output[0])
        self.assertEqual(report["status"], "unavailable")

    def test_doctor_is_json_and_does_not_initialize_provider(self):
        output = []
        def forbidden_provider_factory(config):
            raise AssertionError("doctor must not initialize a provider")
        code = run_cli(
            ["doctor"],
            provider_factory=forbidden_provider_factory,
            readiness_builder=FakeReport,
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output[0])["operating_system"], "TestOS")

    def test_ask_returns_nonzero_when_provider_generation_fails(self):
        class FailingProvider(FakeProvider):
            def generate(self, prompt):
                raise RuntimeError("private payload")

        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: FailingProvider(),
            output_fn=output.append,
        )
        self.assertEqual(code, 4)
        self.assertNotIn("private payload", output[0])

    def test_ask_returns_unavailable_exit_code(self):
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: FakeProvider(available=False),
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        self.assertIn("unavailable", output[0].lower())

    def test_chat_uses_companion_context_and_reset_command(self):
        provider = FakeProvider(answer="Yuki response")
        output = []
        messages = iter(["first message", "follow-up", "/reset", "fresh start", "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            input_fn=lambda prompt: next(messages),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("first message", provider.prompts[1])
        self.assertIn("Yuki response", provider.prompts[1])
        self.assertNotIn("first message", provider.prompts[2])
        self.assertTrue(any("conversation has been cleared" in line for line in output))

    def test_chat_handles_provider_failure_without_leaking_exception(self):
        class FlakyProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def generate(self, prompt):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("secret payload")
                return "recovered"

            def generate_chat(self, system, messages):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("secret payload")
                return "recovered"

        provider = FlakyProvider()
        output = []
        messages = iter(["hello", "retry", "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            input_fn=lambda prompt: next(messages),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertIn("Yuki could not reach the AI provider", output[1])
        self.assertNotIn("secret payload", " ".join(output))
        self.assertIn("Yuki> recovered", output)

    def test_chat_role_command_updates_response_style(self):
        provider = FakeProvider(answer="reviewed")
        output = []
        messages = iter(["/role developer", "review my code", "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            input_fn=lambda prompt: next(messages),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertIn("Interaction style changed to developer", " ".join(output))
        self.assertIn("Current interaction role: developer", provider.prompts[0])
        self.assertIn("Permissions are unchanged", " ".join(output))

    def test_local_auth_cli_bootstrap_login_status_and_logout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = []
            root = Path(temp_dir)
            with patch("yuki_cli.data_root", return_value=root):
                bootstrap_passwords = iter(["Correct horse battery staple 9!", "Correct horse battery staple 9!"])
                code = run_cli(["auth", "bootstrap-admin"], input_fn=lambda prompt: "root.user", password_input=lambda prompt: next(bootstrap_passwords), output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertTrue((root / ".yuki" / "auth.sqlite3").exists())
                login_passwords = iter(["Correct horse battery staple 9!"])
                code = run_cli(["auth", "login"], input_fn=lambda prompt: "root.user", password_input=lambda prompt: next(login_passwords), output_fn=outputs.append)
                self.assertEqual(code, 0)
                session_path = root / ".yuki" / "session.token"
                self.assertTrue(session_path.exists())
                if os.name == "posix":
                    self.assertEqual(session_path.stat().st_mode & 0o777, 0o600)
                code = run_cli(["auth", "status"], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertIn("root.user", outputs[-1])
                code = run_cli(["auth", "logout"], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertFalse(session_path.exists())
                code = run_cli(["auth", "status"], output_fn=outputs.append)
                self.assertEqual(code, 1)

    def test_auth_cli_rejects_mismatched_bootstrap_passwords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = []
            passwords = iter(["first secure password 3!", "second secure password 4!"])
            with patch("yuki_cli.data_root", return_value=Path(temp_dir)):
                code = run_cli(["auth", "bootstrap-admin"], input_fn=lambda prompt: "root.user", password_input=lambda prompt: next(passwords), output_fn=outputs.append)
            self.assertEqual(code, 2)
            self.assertIn("do not match", outputs[-1])
    def test_authenticated_cli_lists_inspects_and_approves_tasks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            auth_db = root / ".yuki" / "auth.sqlite3"
            auth_db.parent.mkdir(parents=True)
            auth = BuiltInAuthService(str(auth_db))
            user = auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
            issued = auth.authenticate("root.user", "Correct horse battery staple 9!")
            session_path = root / ".yuki" / "session.token"
            session_path.write_text(issued.token, encoding="utf-8")
            runtime = TaskRuntime(
                WorkflowRunStore(str(root / ".yuki" / "tasks.sqlite3")),
                authorize=task_permission_authorizer(auth, lambda: session_path.read_text(encoding="utf-8")),
            )
            task = runtime.enqueue(
                actor_id=user.user_id, idempotency_key="cli-task-approval",
                name="Protected task", steps=[{
                    "name": "write", "action": "emit", "argument": "payload",
                    "requires_approval": True,
                }],
            )
            outputs = []
            with patch("yuki_cli.data_root", return_value=root):
                code = run_cli(["tasks", "list"], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertIn(task.task_id, outputs[-1])
                code = run_cli(["tasks", "inspect", task.task_id], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertIn("waiting_approval", outputs[-1])
                code = run_cli(["tasks", "approve", task.task_id, "--version", str(task.version)], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertIn('"state": "queued"', outputs[-1])
            self.assertEqual(runtime.get(actor_id=user.user_id, task_id=task.task_id).state, "queued")

    def test_authenticated_cli_denies_task_commands_after_logout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch("yuki_cli.data_root", return_value=root):
                outputs = []
                code = run_cli(["tasks", "list"], output_fn=outputs.append)
            self.assertEqual(code, 1)
            self.assertIn("Sign in first", outputs[-1])

    def test_authenticated_agent_plan_review_and_dispatch(self):
        class PlanningProvider(FakeProvider):
            def generate_chat(self, system, messages):
                return json.dumps({"tasks": [{
                    "task_id": "implementation",
                    "description": "Prepare an implementation proposal",
                    "required_capabilities": ["coding"],
                    "depends_on": [],
                }]})

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            auth_db = root / ".yuki" / "auth.sqlite3"
            auth_db.parent.mkdir(parents=True)
            auth = BuiltInAuthService(str(auth_db))
            user = auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
            issued = auth.authenticate("root.user", "Correct horse battery staple 9!")
            (root / ".yuki" / "session.token").write_text(issued.token, encoding="utf-8")
            outputs = []
            with patch("yuki_cli.data_root", return_value=root):
                code = run_cli(["agents", "list"], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertIn("coding", outputs[-1])
                code = run_cli(
                    ["agents", "plan", "Build a small application", "--idempotency-key", "cli-agent-plan"],
                    config_loader=lambda: {},
                    provider_factory=lambda config: PlanningProvider(),
                    output_fn=outputs.append,
                )
                self.assertEqual(code, 0)
                proposal = json.loads(outputs[-1])
                self.assertEqual(proposal["subtasks"][0]["agent_id"], "coding")
                self.assertTrue(proposal["subtasks"][0]["requires_approval"])
                self.assertIsNone(proposal["subtasks"][0]["task_id"])
                plan_id = proposal["plan_id"]
                code = run_cli(["agents", "inspect", plan_id], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertIn(plan_id, outputs[-1])
                code = run_cli(["agents", "dispatch", plan_id], output_fn=outputs.append)
                self.assertEqual(code, 0)
                dispatched = json.loads(outputs[-1])
                self.assertEqual(dispatched["subtasks"][0]["status"], "waiting_approval")
                self.assertTrue(dispatched["subtasks"][0]["task_id"])

    def test_signed_out_user_cannot_list_or_plan_agents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = []
            with patch("yuki_cli.data_root", return_value=Path(temp_dir)):
                code = run_cli(["agents", "list"], output_fn=outputs.append)
            self.assertEqual(code, 1)
            self.assertIn("Sign in first", outputs[-1])

    def test_authenticated_worker_cycle_executes_only_approved_specialist_tasks(self):
        class WorkerProvider:
            def is_available(self):
                return True
            def generate(self, prompt):
                return "bounded specialist result"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            auth_db = root / ".yuki" / "auth.sqlite3"
            auth_db.parent.mkdir(parents=True)
            auth = BuiltInAuthService(str(auth_db))
            user = auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
            issued = auth.authenticate("root.user", "Correct horse battery staple 9!")
            (root / ".yuki" / "session.token").write_text(issued.token, encoding="utf-8")
            runtime = TaskRuntime(
                WorkflowRunStore(str(root / ".yuki" / "tasks.sqlite3")),
                authorize=lambda actor, action, _task: (
                    actor == user.user_id and action in {"task:create", "task:approve", "task:read", "task:list"}
                    or actor == "yuki-worker-service" and action in {"task:complete", "task:fail", "task:recover"}
                ),
            )
            task = runtime.enqueue(
                actor_id=user.user_id, idempotency_key="worker-cli-approved",
                name="agent-coding-implementation",
                steps=[{"name": "coding", "action": "emit", "argument": "Review the supplied change", "requires_approval": True}],
            )
            approved = runtime.approve(
                actor_id=user.user_id, task_id=task.task_id, session_id=issued.token,
                expected_version=task.version,
            )
            outputs = []
            with patch("yuki_cli.data_root", return_value=root):
                code = run_cli(
                    ["workers", "run-cycle", "--max-tasks", "2"],
                    config_loader=lambda: {},
                    provider_factory=lambda config: WorkerProvider(),
                    output_fn=outputs.append,
                )
            self.assertEqual(code, 0)
            self.assertIn('"succeeded_count": 1', outputs[-1])
            self.assertEqual(runtime.get(actor_id=user.user_id, task_id=task.task_id).state, "succeeded")

    def test_worker_cycle_requires_sign_in(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = []
            with patch("yuki_cli.data_root", return_value=Path(temp_dir)):
                code = run_cli(["workers", "run-cycle"], output_fn=outputs.append)
            self.assertEqual(code, 1)
            self.assertIn("Sign in first", outputs[-1])

    def test_authenticated_task_event_history_is_bounded_and_incremental(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            auth_db = root / ".yuki" / "auth.sqlite3"
            auth_db.parent.mkdir(parents=True)
            auth = BuiltInAuthService(str(auth_db))
            user = auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
            issued = auth.authenticate("root.user", "Correct horse battery staple 9!")
            (root / ".yuki" / "session.token").write_text(issued.token, encoding="utf-8")
            runtime = TaskRuntime(
                WorkflowRunStore(str(root / ".yuki" / "tasks.sqlite3")),
                authorize=task_permission_authorizer(auth, lambda: issued.token),
            )
            task = runtime.enqueue(
                actor_id=user.user_id, idempotency_key="cli-event-history",
                name="Event history task",
                steps=[{"name": "work", "action": "emit", "argument": "bounded payload"}],
            )
            events = runtime.events(actor_id=user.user_id, task_id=task.task_id)
            self.assertEqual([event.sequence for event in events], [1])
            outputs = []
            with patch("yuki_cli.data_root", return_value=root):
                code = run_cli(["tasks", "events", task.task_id], output_fn=outputs.append)
                self.assertEqual(code, 0)
                payload = json.loads(outputs[-1])
                self.assertEqual(payload[0]["kind"], "task.created")
                self.assertEqual(payload[0]["sequence"], 1)
                code = run_cli(
                    ["tasks", "events", task.task_id, "--after-sequence", "1"],
                    output_fn=outputs.append,
                )
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(outputs[-1]), [])
                code = run_cli(
                    ["tasks", "events", task.task_id, "--limit", "501"],
                    output_fn=outputs.append,
                )
                self.assertEqual(code, 2)

    def test_task_event_history_requires_authentication(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = []
            with patch("yuki_cli.data_root", return_value=Path(temp_dir)):
                code = run_cli(["tasks", "events", "unknown"], output_fn=outputs.append)
            self.assertEqual(code, 1)
            self.assertIn("Sign in first", outputs[-1])

    def test_authenticated_cli_manages_owner_scoped_conversations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            auth_db = root / ".yuki" / "auth.sqlite3"
            auth_db.parent.mkdir(parents=True)
            auth = BuiltInAuthService(str(auth_db))
            auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
            issued = auth.authenticate("root.user", "Correct horse battery staple 9!")
            (root / ".yuki" / "session.token").write_text(issued.token, encoding="utf-8")
            outputs = []
            with patch("yuki_cli.data_root", return_value=root):
                code = run_cli(["conversations", "create", "--title", "Yuki project"], output_fn=outputs.append)
                self.assertEqual(code, 0)
                created = json.loads(outputs[-1])
                self.assertEqual(created["title"], "Yuki project")
                conversation_id = created["conversation_id"]
                code = run_cli(["conversations", "list"], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertIn(conversation_id, outputs[-1])
                code = run_cli(["conversations", "show", conversation_id], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(outputs[-1])["messages"], [])
                code = run_cli(["conversations", "delete", conversation_id], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(outputs[-1])["deleted"], True)

    def test_conversation_cli_requires_authentication(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = []
            with patch("yuki_cli.data_root", return_value=Path(temp_dir)):
                code = run_cli(["conversations", "list"], output_fn=outputs.append)
            self.assertEqual(code, 1)
            self.assertIn("Sign in first", outputs[-1])

    def test_saved_companion_chat_restores_and_persists_each_turn(self):
        from companion.conversation_store import ConversationStore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            auth_db = root / ".yuki" / "auth.sqlite3"
            auth_db.parent.mkdir(parents=True)
            auth = BuiltInAuthService(str(auth_db))
            user = auth.bootstrap_admin("root.user", "Correct horse battery staple 9!")
            issued = auth.authenticate("root.user", "Correct horse battery staple 9!")
            (root / ".yuki" / "session.token").write_text(issued.token, encoding="utf-8")
            store = ConversationStore(str(root / ".yuki" / "conversations.sqlite3"))
            saved = store.create(owner_id=user.user_id, title="Persistent chat")
            provider = FakeProvider(answer="Hello, I am Yuki.")
            inputs = iter(["Hello Yuki", "/quit"])
            outputs = []
            with patch("yuki_cli.data_root", return_value=root):
                code = run_cli(
                    ["chat", "--conversation-id", saved.conversation_id],
                    config_loader=lambda: {},
                    provider_factory=lambda config: provider,
                    input_fn=lambda prompt: next(inputs),
                    output_fn=outputs.append,
                )
            self.assertEqual(code, 0)
            restored = store.get(owner_id=user.user_id, conversation_id=saved.conversation_id)
            self.assertEqual(restored.version, 1)
            self.assertEqual(restored.messages, (
                {"role": "user", "content": "Hello Yuki"},
                {"role": "assistant", "content": "Hello, I am Yuki."},
            ))

    def test_provider_initialization_failure_is_sanitized(self):
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: (_ for _ in ()).throw(RuntimeError("secret payload")),
            output_fn=output.append,
        )
        self.assertEqual(code, 2)
        self.assertNotIn("secret payload", output[0])


if __name__ == "__main__":
    unittest.main()
