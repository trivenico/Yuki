import tempfile
import unittest
from pathlib import Path

from api.runtime import build_conversation_runtime
from api.oauth_authorization import AuthenticatedOAuthAuthorizationAPI
from api.oauth_connections import AuthenticatedOAuthConnectionAPI
from core.agent_execution_loop import AgentExecutionLoop
from core.authorization import AuthorizationDecision, AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway
from core.authorization_audit import SQLiteAuthorizationAuditLog
from core.production_security import ProductionSecurityContext
from core.auth import BuiltInAuthService
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_provider_registry import OAuthProviderPresentation, PreparedOAuthProviderRegistry
from core.mcp_oauth_provider_bootstrap import PreparedOAuthProvider
from types import SimpleNamespace
from core.structured_tool_protocol import StructuredToolProtocol
from providers.authorized_agent_adapter import AuthorizedProviderAgentAdapter



class _MemoryAuditAnchor:
    def __init__(self):
        self.checkpoint = None

    def publish(self, checkpoint):
        self.checkpoint = checkpoint

    def latest(self):
        return self.checkpoint


class _NoApprovals:
    def authorize_tool(self, name):
        return False


def _loop():
    def model(**kwargs):
        return {"text": "Runtime ready", "tool_calls": {"tool_calls": []}}
    protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
    return AgentExecutionLoop(model, protocol)



class _AllowPolicy:
    def authorize(self, request):
        return AuthorizationDecision(True, "decision", "policy-v1", "allowed")


class _Provider:
    model = "test-model"

    def generate_chat(self, **kwargs):
        return {"text": "ok", "tool_calls": {"tool_calls": []}}


def _strict_loop(gateway, principal, *, tool_revision="policy-v1", provider_revision="policy-v1"):
    guarded = GuardedToolRuntime(
        {}, gateway, principal=principal, policy_revision=tool_revision,
    )
    adapter = AuthorizedProviderAgentAdapter(
        _Provider(), authorization_gateway=gateway, principal=principal,
        policy_revision=provider_revision,
    )
    return AgentExecutionLoop(adapter, StructuredToolProtocol(guarded))


class ConversationRuntimeTests(unittest.TestCase):
    def test_shutdown_closes_worker_before_security_runtime_and_is_idempotent(self):
        from api.runtime import ConversationRuntime

        events = []
        class OwnedApplication:
            store = object()
            def close(self):
                events.append("application")

        class OwnedSecurityRuntime:
            security = object()
            def verify_readiness(self):
                events.append("security_ready")
            def close(self):
                events.append("security_close")

        class OwnedWorkerRuntime:
            def verify_readiness(self):
                events.append("worker_ready")
            def close(self):
                events.append("worker_close")

        security_runtime = OwnedSecurityRuntime()
        worker_runtime = OwnedWorkerRuntime()
        runtime = ConversationRuntime(
            object(), OwnedApplication(),
            production_security=security_runtime.security,
            production_security_runtime=security_runtime,
            production_worker_runtime=worker_runtime,
        )
        runtime.close()
        runtime.close()
        self.assertEqual(events, [
            "application", "worker_close", "security_close",
        ])
        self.assertTrue(runtime._closed)

    def test_worker_readiness_endpoint_checks_worker_and_returns_generic_failure(self):
        import asyncio
        from api.runtime import ConversationRuntime

        events = []
        class OwnedApplication:
            store = object()
            def close(self):
                pass

        class OwnedSecurityRuntime:
            security = object()
            def verify_readiness(self):
                events.append("security_ready")
            def close(self):
                pass

        class UnreadyWorkerRuntime:
            def verify_readiness(self):
                events.append("worker_ready")
                raise ConnectionError("private database detail")
            def close(self):
                pass

        security_runtime = OwnedSecurityRuntime()
        runtime = ConversationRuntime(
            object(), OwnedApplication(),
            production_security=security_runtime.security,
            production_security_runtime=security_runtime,
            production_worker_runtime=UnreadyWorkerRuntime(),
        )
        sent = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            sent.append(message)
        asyncio.run(runtime(
            {"type": "http", "method": "GET", "path": "/health/ready", "headers": []},
            receive, send,
        ))
        self.assertEqual(events, ["worker_ready"])
        self.assertEqual(sent[0]["status"], 503)
        self.assertIn(b'"production_runtime":"failed"', sent[1]["body"])
        self.assertNotIn(b"private database detail", sent[1]["body"])



    def test_production_runtime_rejects_stale_guarded_tool_policy_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            audit_log = SQLiteAuthorizationAuditLog(str(root / "authorization-audit.sqlite3"))
            anchor = _MemoryAuditAnchor()
            audit_log.publish_checkpoint(anchor)
            gateway = AuthorizationGateway(_AllowPolicy(), audit=audit_log)
            principal = Principal("alice", "human", tenant_id="tenant-1")
            security = ProductionSecurityContext(
                gateway, lambda actor: principal, lambda worker: Principal(
                    worker, "workload", tenant_id="tenant-1", agent_id="worker",
                ), "policy-v1", anchor,
            )
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                agent_loop_for_owner=lambda owner: _strict_loop(
                    gateway, principal, tool_revision="policy-v0",
                ),
                production_security=security, require_central_authorization=True,
            )
            with self.assertRaisesRegex(ValueError, "owner_agent_loop_policy_revision_mismatch"):
                runtime.application.conversations.agent_loop_for_owner("alice")
            runtime.close()

    def test_production_runtime_rejects_stale_provider_policy_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            audit_log = SQLiteAuthorizationAuditLog(str(root / "authorization-audit.sqlite3"))
            anchor = _MemoryAuditAnchor()
            audit_log.publish_checkpoint(anchor)
            gateway = AuthorizationGateway(_AllowPolicy(), audit=audit_log)
            principal = Principal("alice", "human", tenant_id="tenant-1")
            security = ProductionSecurityContext(
                gateway, lambda actor: principal, lambda worker: Principal(
                    worker, "workload", tenant_id="tenant-1", agent_id="worker",
                ), "policy-v1", anchor,
            )
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                agent_loop_for_owner=lambda owner: _strict_loop(
                    gateway, principal, provider_revision="policy-v0",
                ),
                production_security=security, require_central_authorization=True,
            )
            with self.assertRaisesRegex(ValueError, "owner_provider_adapter_policy_revision_mismatch"):
                runtime.application.conversations.agent_loop_for_owner("alice")
            runtime.close()

    def test_runtime_uses_configured_store_and_closes_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth,
                agent_loop=_loop(),
            )
            self.assertIsInstance(runtime.store, ConversationStore)
            conversation = runtime.app.conversations.create(owner_id="runtime-user")
            self.assertEqual(conversation["owner_id"], "runtime-user")
            runtime.close()
            runtime.close()
            self.assertTrue(runtime._closed)

    def test_runtime_wires_authenticated_owner_execution_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            factory = lambda owner_id: _loop()
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(), agent_loop_for_owner=factory,
            )
            self.assertIs(runtime.app.conversations.agent_loop_for_owner, factory)
            runtime.close()

    def test_runtime_rejects_invalid_owner_execution_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            with self.assertRaisesRegex(TypeError, "agent_loop_for_owner"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=_loop(), agent_loop_for_owner=object(),
                )

    def test_runtime_injects_provider_catalog_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            catalog = lambda: [{"name": "ollama", "ready": True}]
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(), provider_catalog=catalog,
            )
            self.assertIs(runtime.app.provider_catalog, catalog)
            self.assertEqual(runtime.app.provider_catalog()[0]["name"], "ollama")
            runtime.close()

    def test_runtime_injects_provider_selection_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            selector = lambda name: {"name": name}
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(), provider_selector=selector,
            )
            self.assertIs(runtime.app.provider_selector, selector)
            runtime.close()

    def test_runtime_mounts_oauth_apis_with_trusted_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            registry = SQLiteOAuthConnectionRegistry(str(root / "oauth.sqlite"))
            authorization_resolver = lambda provider: None
            disconnect_resolver = lambda provider: None
            catalog = lambda: [{"provider_id": "example", "available": False}]
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                oauth_connections=registry,
                oauth_disconnect_service_for_provider=disconnect_resolver,
                oauth_authorization_service_for_provider=authorization_resolver,
                oauth_provider_catalog=catalog,
            )
            self.assertIsInstance(runtime.app, AuthenticatedOAuthAuthorizationAPI)
            self.assertIsInstance(runtime.app.app, AuthenticatedOAuthConnectionAPI)
            self.assertIs(runtime.app._provider_catalog, catalog)
            runtime.close()

    def test_runtime_wires_prepared_registry_resolver_and_safe_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            registry = SQLiteOAuthConnectionRegistry(str(root / "oauth.sqlite"))
            service = SimpleNamespace(begin=lambda **kwargs: {}, complete=lambda **kwargs: {})
            prepared = PreparedOAuthProvider(
                provider_id="example", issuer="https://issuer.example",
                resource="https://mcp.example", client_id="private-client",
                registration_method="pre_registered",
                redirect_uri="https://app.example/oauth/callback/example",
                scopes=("read",), authorization_service=service,
            )
            providers = PreparedOAuthProviderRegistry(
                {"example": prepared},
                {"example": OAuthProviderPresentation("Example", "Trusted integration")},
            )
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                oauth_connections=registry,
                oauth_disconnect_service_for_provider=lambda provider: None,
                prepared_oauth_provider_registry=providers,
            )
            self.assertIs(runtime.app._service_for_provider("example"), service)
            self.assertEqual(runtime.app._provider_catalog(), ({
                "provider_id": "example", "display_name": "Example",
                "description": "Trusted integration", "available": True,
            },))
            self.assertIsNone(runtime.app._service_for_provider("unknown"))
            runtime.close()

    def test_runtime_rejects_registry_combined_with_explicit_oauth_resolver(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            connection_registry = SQLiteOAuthConnectionRegistry(str(root / "oauth.sqlite"))
            service = SimpleNamespace(begin=lambda **kwargs: {}, complete=lambda **kwargs: {})
            prepared = PreparedOAuthProvider(
                provider_id="example", issuer="https://issuer.example",
                resource="https://mcp.example", client_id="private-client",
                registration_method="pre_registered",
                redirect_uri="https://app.example/oauth/callback/example",
                scopes=("read",), authorization_service=service,
            )
            providers = PreparedOAuthProviderRegistry(
                {"example": prepared}, {"example": OAuthProviderPresentation("Example")}
            )
            with self.assertRaisesRegex(ValueError, "cannot be combined"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=_loop(),
                    oauth_connections=connection_registry,
                    oauth_disconnect_service_for_provider=lambda provider: None,
                    oauth_authorization_service_for_provider=lambda provider: service,
                    prepared_oauth_provider_registry=providers,
                )

    def test_runtime_rejects_oauth_catalog_without_authorization_resolver(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            with self.assertRaisesRegex(ValueError, "requires authorization service"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=_loop(),
                    oauth_provider_catalog=lambda: [],
                )

    def test_asgi_lifespan_closes_storage_on_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
            )
            events = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
            sent = []
            async def receive():
                return events.pop(0)
            async def send(message):
                sent.append(message)
            import asyncio
            asyncio.run(runtime({"type": "lifespan"}, receive, send))
            self.assertEqual(sent, [
                {"type": "lifespan.startup.complete"},
                {"type": "lifespan.shutdown.complete"},
            ])
            self.assertTrue(runtime._closed)

    def test_invalid_dependencies_fail_before_store_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TypeError, "auth_service"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=object(), agent_loop=_loop(),
                )

    def _strict_runtime_with_audit(self, directory):
        root = Path(directory)
        auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        audit_log = SQLiteAuthorizationAuditLog(str(root / "authorization-audit.sqlite3"))
        anchor = _MemoryAuditAnchor()
        audit_log.publish_checkpoint(anchor)
        gateway = AuthorizationGateway(_AllowPolicy(), audit=audit_log)
        security = ProductionSecurityContext(
            gateway,
            lambda owner: Principal(owner, "human", tenant_id="tenant-1"),
            lambda worker: Principal(worker, "workload", tenant_id="tenant-1", agent_id="worker"),
            "policy-v1",
            anchor,
        )
        runtime = build_conversation_runtime(
            {"storage_backend": "sqlite", "workspace": directory},
            auth_service=auth,
            agent_loop=_loop(),
            agent_loop_for_owner=lambda owner: _strict_loop(
                gateway, Principal(owner, "human", tenant_id="tenant-1"),
            ),
            production_security=security,
            require_central_authorization=True,
        )
        return runtime, audit_log

    def test_production_readiness_endpoint_reports_verified_audit_chain(self):
        import asyncio
        with tempfile.TemporaryDirectory() as directory:
            runtime, _audit_log = self._strict_runtime_with_audit(directory)
            events = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                events.append(message)
            asyncio.run(runtime(
                {"type": "http", "method": "GET", "path": "/health/ready", "headers": []},
                receive, send,
            ))
            self.assertEqual(events[0]["status"], 200)
            self.assertIn(b'"authorization_audit":"ok"', events[1]["body"])
            runtime.close()

    def test_production_readiness_endpoint_fails_closed_after_audit_tampering(self):
        import asyncio
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            runtime, audit_log = self._strict_runtime_with_audit(directory)
            audit_log(
                AuthorizationRequest(Principal("alice", "human"), "task:read", "task:one"),
                AuthorizationDecision(True, "decision-1", "policy-v1", "allowed"),
            )
            with sqlite3.connect(audit_log.database_path) as db:
                db.execute("UPDATE authorization_audit_events SET reason_code='tampered'")
            events = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                events.append(message)
            asyncio.run(runtime(
                {"type": "http", "method": "GET", "path": "/health/ready", "headers": []},
                receive, send,
            ))
            self.assertEqual(events[0]["status"], 503)
            self.assertIn(b'"authorization_audit":"failed"', events[1]["body"])
            runtime.close()


    def test_production_security_runtime_is_checked_at_startup_and_closed_on_shutdown(self):
        import asyncio
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            audit_log = SQLiteAuthorizationAuditLog(str(root / "authorization-audit.sqlite3"))
            anchor = _MemoryAuditAnchor()
            audit_log.publish_checkpoint(anchor)
            gateway = AuthorizationGateway(_AllowPolicy(), audit=audit_log)
            security = ProductionSecurityContext(
                gateway,
                lambda owner: Principal(owner, "human", tenant_id="tenant-1"),
                lambda worker: Principal(worker, "workload", tenant_id="tenant-1", agent_id="worker"),
                "policy-v1", anchor,
            )
            class OwnedSecurityRuntime:
                def __init__(self):
                    self.security = security
                    self.readiness_calls = 0
                    self.close_calls = 0
                def verify_readiness(self):
                    self.readiness_calls += 1
                    return True
                def close(self):
                    self.close_calls += 1
            owned = OwnedSecurityRuntime()
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                agent_loop_for_owner=lambda owner: _strict_loop(
                    gateway, Principal(owner, "human", tenant_id="tenant-1"),
                ),
                production_security_runtime=owned,
                require_central_authorization=True,
            )
            events = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
            sent = []
            async def receive():
                return events.pop(0)
            async def send(message):
                sent.append(message)
            asyncio.run(runtime({"type": "lifespan"}, receive, send))
            self.assertEqual(owned.readiness_calls, 1)
            self.assertEqual(owned.close_calls, 1)
            self.assertEqual(sent, [
                {"type": "lifespan.startup.complete"},
                {"type": "lifespan.shutdown.complete"},
            ])
            runtime.close()
            self.assertEqual(owned.close_calls, 1)

    def test_production_security_runtime_startup_fails_closed_when_not_ready(self):
        import asyncio
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            audit_log = SQLiteAuthorizationAuditLog(str(root / "authorization-audit.sqlite3"))
            anchor = _MemoryAuditAnchor()
            audit_log.publish_checkpoint(anchor)
            gateway = AuthorizationGateway(_AllowPolicy(), audit=audit_log)
            security = ProductionSecurityContext(
                gateway,
                lambda owner: Principal(owner, "human", tenant_id="tenant-1"),
                lambda worker: Principal(worker, "workload", tenant_id="tenant-1", agent_id="worker"),
                "policy-v1", anchor,
            )
            class UnreadySecurityRuntime:
                def __init__(self):
                    self.security = security
                    self.close_calls = 0
                def verify_readiness(self):
                    raise ConnectionError("database unavailable")
                def close(self):
                    self.close_calls += 1
            owned = UnreadySecurityRuntime()
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                agent_loop_for_owner=lambda owner: _strict_loop(
                    gateway, Principal(owner, "human", tenant_id="tenant-1"),
                ),
                production_security_runtime=owned,
                require_central_authorization=True,
            )
            events = [{"type": "lifespan.startup"}]
            sent = []
            async def receive():
                return events.pop(0)
            async def send(message):
                sent.append(message)
            asyncio.run(runtime({"type": "lifespan"}, receive, send))
            self.assertEqual(sent, [{
                "type": "lifespan.startup.failed",
                "message": "production_security_runtime_not_ready",
            }])
            runtime.close()
            self.assertEqual(owned.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
