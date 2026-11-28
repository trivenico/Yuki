import json
import tempfile
import unittest
from pathlib import Path

from core.worker_health import write_worker_health

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


class FakeReport:
    def to_dict(self):
        return {"operating_system": "TestOS", "capabilities": {}}


class YukiCliTests(unittest.TestCase):

    def test_recovery_list_requires_admin_and_only_reads_existing_audit_db(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".yuki"
            root.mkdir(mode=0o700)
            (root / "auth.sqlite3").touch()
            audit_path = Path(directory) / "approval-audit.sqlite3"
            audit_path.touch()

            class Auth:
                revoked = []

                def authenticate(self, username, password):
                    self.asserted = (username, password)
                    user = SimpleNamespace(
                        role="admin", permissions=frozenset({"workflow:approve"}),
                    )
                    return SimpleNamespace(token="session-token", user=user)

                def revoke_session(self, token):
                    self.revoked.append(token)

            class Audit:
                def list_unresolved_executions(self, *, limit):
                    self.limit = limit
                    return [{
                        "started_event_id": 7,
                        "pending_id_hash": "a" * 64,
                        "actor_hash": "b" * 64,
                        "skill_id": "notes.write",
                        "started_at": "2026-11-28T00:00:00+00:00",
                    }]

            auth = Auth()
            audit = Audit()
            output = []
            with patch("yuki_cli.desktop_data_root", return_value=root):
                code = run_cli(
                    ["auth", "recovery-list", "--audit-db", str(audit_path)],
                    input_fn=lambda _prompt: "owner",
                    password_input=lambda _prompt: "correct-password",
                    auth_service_factory=lambda _path: auth,
                    audit_log_factory=lambda _path: audit,
                    output_fn=output.append,
                )
            self.assertEqual(code, 0)
            report = json.loads(output[0])
            self.assertEqual(report["recovery_required_count"], 1)
            self.assertTrue(report["read_only"])
            self.assertFalse(report["execution_started"])
            self.assertFalse(report["automatic_retry"])
            self.assertEqual(audit.limit, 100)
            self.assertEqual(auth.revoked, ["session-token"])

    def test_consistency_check_requires_owner_and_uses_one_shared_fence(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".yuki"
            root.mkdir(mode=0o700)
            (root / "auth.sqlite3").touch()
            paths = {
                name: Path(directory) / (name + ".sqlite3")
                for name in ("pending", "audit", "grants")
            }
            for path in paths.values():
                path.touch()

            class Auth:
                revoked = []

                def authenticate(self, username, password):
                    return SimpleNamespace(
                        token="owner-session",
                        user=SimpleNamespace(
                            user_id="owner-id", role="admin",
                            permissions=frozenset({"workflow:approve"}),
                        ),
                    )

                def revoke_session(self, token):
                    self.revoked.append(token)

            class Store:
                def __init__(self, path, maintenance_fence):
                    self.path = path
                    self.maintenance_fence = maintenance_fence

            auth = Auth()
            captured = {}
            def scanner(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    generated_at="2026-11-28T00:00:00+00:00",
                    audit_chain_valid=True,
                    complete=True,
                    finding_count=1,
                    findings=({"kind": "expired_one_shot_grant"},),
                    warnings=(),
                )

            output = []
            with patch("yuki_cli.desktop_data_root", return_value=root):
                code = run_cli(
                    ["auth", "consistency-check",
                     "--pending-db", str(paths["pending"]),
                     "--audit-db", str(paths["audit"]),
                     "--grants-db", str(paths["grants"])],
                    input_fn=lambda _prompt: "owner",
                    password_input=lambda _prompt: "owner-password",
                    auth_service_factory=lambda _path: auth,
                    pending_store_factory=lambda path, **kwargs: Store(path, **kwargs["maintenance_fence"]),
                    audit_log_factory=lambda path, **kwargs: Store(path, **kwargs["maintenance_fence"]),
                    grant_store_factory=lambda path, **kwargs: Store(path, **kwargs["maintenance_fence"]),
                    recovery_scanner=scanner,
                    output_fn=output.append,
                )
            self.assertEqual(code, 0)
            report = json.loads(output[0])
            self.assertTrue(report["read_only"])
            self.assertFalse(report["records_deleted"])
            self.assertFalse(report["grants_revoked"])
            self.assertFalse(report["actions_executed"])
            fences = [
                captured["pending_store"].maintenance_fence,
                captured["audit_log"].maintenance_fence,
                captured["grant_store"].maintenance_fence,
                captured["maintenance_fence"],
            ]
            self.assertTrue(all(item is fences[0] for item in fences))
            self.assertEqual(auth.revoked, ["owner-session"])

    def test_consistency_check_refuses_missing_store_without_creating_it(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".yuki"
            root.mkdir(mode=0o700)
            (root / "auth.sqlite3").touch()
            pending = Path(directory) / "pending.sqlite3"
            audit = Path(directory) / "audit.sqlite3"
            grants = Path(directory) / "missing-grants.sqlite3"
            pending.touch()
            audit.touch()
            calls = []
            output = []
            with patch("yuki_cli.desktop_data_root", return_value=root):
                code = run_cli(
                    ["auth", "consistency-check",
                     "--pending-db", str(pending),
                     "--audit-db", str(audit),
                     "--grants-db", str(grants)],
                    pending_store_factory=lambda *_a, **_k: calls.append("pending"),
                    audit_log_factory=lambda *_a, **_k: calls.append("audit"),
                    grant_store_factory=lambda *_a, **_k: calls.append("grants"),
                    recovery_scanner=lambda **_kwargs: calls.append("scan"),
                    output_fn=output.append,
                )
            self.assertEqual(code, 2)
            self.assertFalse(grants.exists())
            self.assertEqual(calls, [])
            self.assertIn("FileNotFoundError", output[0])

    def test_reconcile_requires_owner_login_and_explicit_evidence(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".yuki"
            root.mkdir(mode=0o700)
            (root / "auth.sqlite3").touch()
            audit_path = Path(directory) / "approval-audit.sqlite3"
            audit_path.touch()

            class Auth:
                revoked = []

                def authenticate(self, username, password):
                    return SimpleNamespace(
                        token="owner-session",
                        user=SimpleNamespace(
                            user_id="owner-id", role="admin",
                            permissions=frozenset({"workflow:approve"}),
                        ),
                    )

                def revoke_session(self, token):
                    self.revoked.append(token)

            class Audit:
                call = None

                def reconcile_unresolved_execution(self, started_event_id, **kwargs):
                    self.call = (started_event_id, kwargs)
                    return 12

            auth, audit = Auth(), Audit()
            output = []
            inputs = iter(["owner", "evidence:incident-42"])
            with patch("yuki_cli.desktop_data_root", return_value=root):
                code = run_cli(
                    ["auth", "reconcile", "7", "--audit-db", str(audit_path),
                     "--resolution", "not_executed"],
                    input_fn=lambda _prompt: next(inputs),
                    password_input=lambda _prompt: "owner-password",
                    auth_service_factory=lambda _path: auth,
                    audit_log_factory=lambda _path: audit,
                    identity_validator_factory=lambda _auth, session_token_provider:
                        (lambda actor, session: actor == "owner-id" and session == session_token_provider()),
                    output_fn=output.append,
                )
            self.assertEqual(code, 0)
            self.assertEqual(audit.call[0], 7)
            self.assertEqual(audit.call[1]["resolution"], "not_executed")
            self.assertEqual(audit.call[1]["evidence"], "evidence:incident-42")
            report = json.loads(output[0])
            self.assertEqual(report["status"], "reconciled")
            self.assertFalse(report["action_replayed"])
            self.assertFalse(report["automatic_retry_enabled"])
            self.assertTrue(report["original_audit_events_preserved"])
            self.assertEqual(auth.revoked, ["owner-session"])

    def test_reconcile_rejects_blank_evidence_before_writing(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".yuki"
            root.mkdir(mode=0o700)
            (root / "auth.sqlite3").touch()
            audit_path = Path(directory) / "approval-audit.sqlite3"
            audit_path.touch()

            class Auth:
                def authenticate(self, username, password):
                    return SimpleNamespace(
                        token="owner-session",
                        user=SimpleNamespace(
                            user_id="owner-id", role="admin",
                            permissions=frozenset({"workflow:approve"}),
                        ),
                    )

                def revoke_session(self, token):
                    pass

            audit_calls = []
            output = []
            inputs = iter(["owner", "   "])
            with patch("yuki_cli.desktop_data_root", return_value=root):
                code = run_cli(
                    ["auth", "reconcile", "7", "--audit-db", str(audit_path),
                     "--resolution", "executed"],
                    input_fn=lambda _prompt: next(inputs),
                    password_input=lambda _prompt: "owner-password",
                    auth_service_factory=lambda _path: Auth(),
                    audit_log_factory=lambda _path: audit_calls.append("called"),
                    identity_validator_factory=lambda *_args, **_kwargs: lambda *_a: True,
                    output_fn=output.append,
                )
            self.assertEqual(code, 2)
            self.assertEqual(audit_calls, [])
            self.assertIn("ValueError", output[0])

    def test_recovery_list_does_not_create_missing_audit_database(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".yuki"
            root.mkdir(mode=0o700)
            (root / "auth.sqlite3").touch()
            missing = Path(directory) / "missing-audit.sqlite3"
            output = []
            audit_factory_calls = []
            with patch("yuki_cli.desktop_data_root", return_value=root):
                code = run_cli(
                    ["auth", "recovery-list", "--audit-db", str(missing)],
                    auth_service_factory=lambda _path: object(),
                    audit_log_factory=lambda path: audit_factory_calls.append(path),
                    output_fn=output.append,
                )
            self.assertEqual(code, 2)
            self.assertFalse(missing.exists())
            self.assertEqual(audit_factory_calls, [])
            self.assertIn("FileNotFoundError", output[0])

    def test_worker_status_is_read_only_and_does_not_initialize_provider(self):
        class Resources:
            logical_cpu_count = 4
            effective_cpu_count = 2
            cpu_cgroup_limit = 1.5
            cpu_cgroup_source = "cgroup_v2"
            memory_total_bytes = 1024 * 1024 * 1024
            memory_available_bytes = 700 * 1024 * 1024
            memory_source = "test"
            memory_cgroup_limit_bytes = 800 * 1024 * 1024
            memory_cgroup_available_bytes = 512 * 1024 * 1024
            memory_effective_available_bytes = 512 * 1024 * 1024

        output = []
        def forbidden_provider_factory(config):
            raise AssertionError("worker status must not initialize a provider")
        def forbidden_config_loader():
            raise AssertionError("worker status must not load application config")

        code = run_cli(
            ["worker", "status"],
            provider_factory=forbidden_provider_factory,
            config_loader=forbidden_config_loader,
            resource_inventory_builder=Resources,
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        report = json.loads(output[0])
        self.assertEqual(report["worker_runtime"], "not_started_by_status_command")
        self.assertEqual(report["default_max_workers"], 1)
        self.assertEqual(report["logical_cpu_count"], 4)
        self.assertEqual(report["effective_cpu_count"], 2)
        self.assertEqual(report["cpu_cgroup_limit"], 1.5)
        self.assertEqual(report["cpu_cgroup_source"], "cgroup_v2")
        self.assertEqual(report["cpu"]["effective_count"], 2)
        self.assertEqual(report["cpu"]["cgroup_source"], "cgroup_v2")
        self.assertEqual(report["worker_budget"]["allowed_concurrency"], 1)
        self.assertEqual(report["resource_profile"], "single_foreground_worker")
        self.assertEqual(report["memory_guard"]["state"], "not_active_worker_not_started")
        self.assertEqual(report["memory_total_bytes"], 1024 * 1024 * 1024)
        self.assertEqual(report["memory_available_bytes"], 700 * 1024 * 1024)
        self.assertEqual(report["memory_source"], "test")
        self.assertEqual(report["memory_cgroup_limit_bytes"], 800 * 1024 * 1024)
        self.assertEqual(report["memory_cgroup_available_bytes"], 512 * 1024 * 1024)
        self.assertEqual(report["memory_effective_available_bytes"], 512 * 1024 * 1024)
        self.assertEqual(report["memory_guard_state"], "process_local_not_persisted")
        self.assertFalse(report["task_execution_started"])

    def test_worker_status_reads_explicit_health_snapshot_without_starting_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if __import__("os").name == "posix":
                root.chmod(0o700)
            snapshot = root / "worker-health.json"
            write_worker_health(snapshot, details={"phase": "idle", "active_cycles": 0})
            output = []

            class Resources:
                logical_cpu_count = 2
                memory_total_bytes = 1024
                memory_available_bytes = 512
                memory_source = "test"
                memory_cgroup_limit_bytes = None
                memory_cgroup_available_bytes = None
                memory_effective_available_bytes = 512

            code = run_cli(
                ["worker", "status", "--health-file", str(snapshot)],
                resource_inventory_builder=Resources,
                output_fn=output.append,
                provider_factory=lambda _config: (_ for _ in ()).throw(
                    AssertionError("status must not initialize a provider")
                ),
            )
            report = json.loads(output[0])
            self.assertEqual(code, 0)
            self.assertEqual(report["health_snapshot"]["state"], "fresh")
            self.assertEqual(report["health_snapshot"]["details"]["phase"], "idle")
            self.assertFalse(report["task_execution_started"])

    def test_chat_uses_persistent_runtime_and_closes_it(self):
        class Conversations:
            def __init__(self):
                self.items = []
                self.history_calls = 0
                self.reply_calls = 0

            def list(self, *, owner_id, limit):
                return list(self.items[:limit])

            def create(self, *, owner_id, title="New conversation"):
                item = {"conversation_id": "conversation-1234567890", "title": title, "version": 0}
                self.items.insert(0, item)
                return item

            def history(self, *, owner_id, conversation_id, limit=100):
                self.history_calls += 1
                return [{"role": "user", "content": "hello"}]

            def reply(self, **kwargs):
                self.reply_calls += 1
                return {"answer": "saved answer", "version": 2, "status": "completed"}

        class Runtime:
            def __init__(self):
                self.owner_id = "local-test-owner"
                self.conversations = Conversations()
                self.closed = False

            def close(self):
                self.closed = True

        runtime = Runtime()
        output = []
        inputs = iter(["hello", "/history", "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            conversation_runtime_factory=lambda config: runtime,
            input_fn=lambda prompt: next(inputs),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertTrue(runtime.closed)
        self.assertEqual(runtime.conversations.reply_calls, 1)
        self.assertEqual(runtime.conversations.history_calls, 1)
        self.assertIn("Yuki> saved answer", output)

    def test_chat_recovers_control_after_generation_keyboard_interrupt(self):
        class Conversations:
            def __init__(self):
                self.items = []
                self.reply_calls = 0

            def list(self, *, owner_id, limit):
                return list(self.items[:limit])

            def create(self, *, owner_id, title="New conversation"):
                item = {"conversation_id": "conversation-interrupt-12345",
                        "title": title, "version": 0}
                self.items.insert(0, item)
                return item

            def reply(self, **kwargs):
                self.reply_calls += 1
                raise KeyboardInterrupt()

        class Runtime:
            owner_id = "local-test-owner"

            def __init__(self):
                self.conversations = Conversations()
                self.closed = False

            def close(self):
                self.closed = True

        runtime = Runtime()
        output = []
        inputs = iter(["hello", "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            conversation_runtime_factory=lambda config: runtime,
            input_fn=lambda prompt: next(inputs),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertTrue(runtime.closed)
        self.assertEqual(runtime.conversations.reply_calls, 1)
        self.assertTrue(any("Generation interrupted" in line for line in output))

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

    def test_provider_test_sends_explicit_fixed_probe_and_hides_response(self):
        provider = FakeProvider(answer="YUKI_CONNECTION_OK")
        output = []
        code = run_cli(
            ["provider-test"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(provider.prompts, ["Reply with exactly: YUKI_CONNECTION_OK"])
        report = json.loads(output[0])
        self.assertEqual(report["status"], "connection_verified")
        self.assertTrue(report["verified"])
        self.assertNotIn("YUKI_CONNECTION_OK", output[0])

    def test_provider_test_reports_failure_without_leaking_provider_error(self):
        class FailingProvider(FakeProvider):
            def generate(self, prompt):
                raise RuntimeError("private API response and secret")

        output = []
        code = run_cli(
            ["provider-test"],
            config_loader=lambda: {},
            provider_factory=lambda config: FailingProvider(),
            output_fn=output.append,
        )
        self.assertEqual(code, 4)
        report = json.loads(output[0])
        self.assertEqual(report["status"], "request_failed")
        self.assertEqual(report["error_type"], "RuntimeError")
        self.assertNotIn("private API response", output[0])
        self.assertNotIn("secret", output[0])

    def test_provider_test_does_not_send_request_when_unavailable(self):
        provider = FakeProvider(available=False)
        output = []
        code = run_cli(
            ["provider-test"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        self.assertEqual(provider.prompts, [])
        self.assertEqual(json.loads(output[0])["status"], "unavailable")

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

    def test_provider_status_does_not_claim_api_key_presence_proves_connection(self):
        class ConfiguredProvider(FakeProvider):
            availability_scope = "api_key_present_only"

        output = []
        code = run_cli(
            ["provider-status"],
            config_loader=lambda: {},
            provider_factory=lambda config: ConfiguredProvider(available=True),
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        report = json.loads(output[0])
        self.assertEqual(report["status"], "configured_connection_unverified")
        self.assertEqual(report["availability_scope"], "api_key_present_only")

    def test_provider_status_does_not_claim_gemini_executable_proves_authentication(self):
        class InstalledGemini(FakeProvider):
            availability_scope = "executable_installed_only"

        output = []
        code = run_cli(
            ["provider-status"],
            config_loader=lambda: {},
            provider_factory=lambda config: InstalledGemini(available=True),
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        report = json.loads(output[0])
        self.assertEqual(report["status"], "installed_authentication_unverified")
        self.assertEqual(report["availability_scope"], "executable_installed_only")

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
