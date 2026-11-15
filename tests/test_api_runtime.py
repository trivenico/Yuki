import tempfile
import threading
import unittest
from unittest.mock import Mock
from pathlib import Path

from api.runtime import ConversationRuntime, build_conversation_runtime
from api.oauth_authorization import AuthenticatedOAuthAuthorizationAPI
from api.oauth_connections import AuthenticatedOAuthConnectionAPI
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.core_component_runtime import build_core_component_registry
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_provider_registry import OAuthProviderPresentation, PreparedOAuthProviderRegistry
from core.mcp_oauth_provider_bootstrap import PreparedOAuthProvider
from types import SimpleNamespace
from core.structured_tool_protocol import StructuredToolProtocol
from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.google_gmail_runtime import build_gmail_tool_runtime_bundle
from core.google_gmail_send_client import GmailSendClient
from core.google_gmail_readonly_client import GmailReadOnlyClient
from core.tool_action_journal import ToolActionJournal
from core.tool_capabilities import ToolCapabilityCatalog
from core.google_gmail_send_tool import GMAIL_SEND_TOOL_NAME


class _NoApprovals:
    def authorize_tool(self, name):
        return False


def _loop():
    def model(**kwargs):
        return {"text": "Runtime ready", "tool_calls": {"tool_calls": []}}
    protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
    return AgentExecutionLoop(model, protocol)



class _GmailSendClient(GmailSendClient):
    def __init__(self):
        # Composition tests use a non-networking test double; no credential
        # resolver or transport is needed because send_email is never called.
        pass

    def send_email(self, **kwargs):
        raise AssertionError("API composition test must not send email")


class _GmailReadClient(GmailReadOnlyClient):
    def __init__(self):
        # The test double is only registered for composition identity checks.
        pass

    def find_message_by_rfc822_message_id(self, **kwargs):
        return {"found": False}


class _GmailPolicy:
    def authorize_tool(self, name):
        return name == GMAIL_SEND_TOOL_NAME


class ConversationRuntimeTests(unittest.TestCase):
    def test_runtime_composes_gmail_bundle_into_authenticated_api_and_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            approvals = SQLiteToolActionApprovalStore(str(root / "approvals.sqlite"))
            proposals = SQLitePendingToolActionProposalStore(str(root / "proposals.sqlite"))
            bundle = build_gmail_tool_runtime_bundle(
                existing_tools={}, existing_capabilities=ToolCapabilityCatalog({}),
                existing_tool_definitions=(), policy=_GmailPolicy(),
                action_journal=ToolActionJournal(str(root / "journal.sqlite")),
                approval_store=approvals, proposals=proposals,
                send_client=_GmailSendClient(), readonly_client=_GmailReadClient(),
                message_id_domain="mail.example.com",
            )
            agent = AgentExecutionLoop(
                lambda **kwargs: {"text": "ready", "tool_calls": {"tool_calls": []}},
                bundle.tool_protocol,
            )
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=agent, gmail_runtime_bundle=bundle,
                tools_for_user=lambda owner: [dict(item) for item in bundle.tool_definitions]
                if owner == "gmail-owner" else [],
            )
            self.assertIs(runtime.app.tool_action_approvals, approvals)
            self.assertIs(runtime.app.pending_tool_action_proposals, proposals)
            self.assertIs(runtime.app.approved_tool_action_executor.guarded_runtime,
                          bundle.guarded_runtime)
            self.assertIs(runtime.app.tool_action_reconciler, bundle.reconciliation_service)
            definitions = runtime.app.tools_for_user("gmail-owner")
            self.assertEqual([item["function"]["name"] for item in definitions],
                             [GMAIL_SEND_TOOL_NAME])
            self.assertEqual(runtime.app.tools_for_user("other-owner"), [])
            self.assertIs(runtime.application.conversations.agent_loop.tool_protocol,
                          bundle.tool_protocol)
            runtime.close()

    def test_runtime_rejects_gmail_bundle_with_different_agent_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            bundle = build_gmail_tool_runtime_bundle(
                existing_tools={}, existing_capabilities=ToolCapabilityCatalog({}),
                existing_tool_definitions=(), policy=_GmailPolicy(),
                action_journal=ToolActionJournal(str(root / "journal.sqlite")),
                approval_store=SQLiteToolActionApprovalStore(str(root / "approvals.sqlite")),
                proposals=SQLitePendingToolActionProposalStore(str(root / "proposals.sqlite")),
                send_client=_GmailSendClient(), readonly_client=_GmailReadClient(),
                message_id_domain="mail.example.com",
            )
            with self.assertRaisesRegex(ValueError, "exact structured tool protocol"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=_loop(), gmail_runtime_bundle=bundle,
                )

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

    def test_runtime_wires_optional_tool_action_approval_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            approvals = SQLiteToolActionApprovalStore(str(root / "approvals.sqlite"))
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                tool_action_approvals=approvals,
            )
            self.assertIs(runtime.app.tool_action_approvals, approvals)
            runtime.close()

    def test_runtime_wires_trusted_action_callbacks_to_conversation_service(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            action_factory = lambda principal, name, arguments: None
            confirmation = lambda call: False
            approval_factory = lambda principal, context, call: None
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                action_context_factory=action_factory,
                confirmation_check=confirmation,
                approval_id_factory=approval_factory,
            )
            service = runtime.application.conversations
            self.assertIs(service.action_context_factory, action_factory)
            self.assertIs(service.confirmation_check, confirmation)
            self.assertIs(service.approval_id_factory, approval_factory)
            runtime.close()

    def test_durable_approval_store_gets_safe_default_context_and_token_adapters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            approvals = SQLiteToolActionApprovalStore(str(root / "approvals.sqlite"))
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                tool_action_approvals=approvals,
            )
            service = runtime.application.conversations
            self.assertTrue(callable(service.action_context_factory))
            self.assertIsNone(service.confirmation_check)
            self.assertTrue(callable(service.approval_id_factory))
            context = service.action_context_factory(
                "authenticated-user", "email.send",
                {"to": "person@example.com", "subject": "Hello"},
            )
            token = service.approval_id_factory(
                "authenticated-user", context, SimpleNamespace(name="email.send"),
            )
            self.assertIsInstance(token, str)
            self.assertTrue(approvals.consume(
                token, context, principal_id="authenticated-user",
            ))
            self.assertFalse(approvals.consume(
                token, context, principal_id="authenticated-user",
            ))
            runtime.close()

    def test_runtime_injects_pending_action_proposal_store_into_authenticated_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            proposals = SQLitePendingToolActionProposalStore(
                str(root / "proposals.sqlite"),
            )
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                pending_tool_action_proposals=proposals,
            )
            self.assertIs(runtime.app.pending_tool_action_proposals, proposals)
            runtime.close()

    def test_concurrent_close_waits_for_api_component_shutdown(self):
        entered = threading.Event()
        release = threading.Event()
        second_attempting = threading.Event()
        second_finished = threading.Event()

        class BlockingRegistry:
            def __init__(self):
                self.calls = 0

            def close(self):
                self.calls += 1
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test shutdown release timed out")
                return ()

        registry = BlockingRegistry()
        application = SimpleNamespace(store=object())
        runtime = ConversationRuntime(lambda scope, receive, send: None, application, registry)
        first = threading.Thread(target=runtime.close)
        second = threading.Thread(
            target=lambda: (second_attempting.set(), runtime.close(), second_finished.set())
        )
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        self.assertTrue(second_attempting.wait(2))
        self.assertFalse(second_finished.wait(0.05))
        release.set()
        first.join(2)
        second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertTrue(second_finished.is_set())
        self.assertEqual(registry.calls, 1)

    def test_runtime_close_is_not_retried_when_application_cleanup_fails(self):
        application = Mock()
        application.store = object()
        application.close.side_effect = RuntimeError("cleanup failed")
        runtime = ConversationRuntime(lambda scope, receive, send: None, application)

        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            runtime.close()
        runtime.close()

        self.assertTrue(runtime._closed)
        application.close.assert_called_once_with()

    def test_runtime_rejects_http_dispatch_after_shutdown(self):
        application = Mock()
        application.store = object()
        runtime = ConversationRuntime(lambda scope, receive, send: None, application)
        runtime.close()
        dispatched = []

        async def web(scope, receive, send):
            dispatched.append(scope)

        runtime.web = web

        import asyncio
        async def invoke():
            async def receive():
                return {"type": "http.request"}
            async def send(message):
                return None
            await runtime({"type": "http"}, receive, send)

        with self.assertRaisesRegex(RuntimeError, "runtime is closed"):
            asyncio.run(invoke())
        self.assertEqual(dispatched, [])

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
            service = SimpleNamespace(provider_id="example", begin=lambda **kwargs: {}, complete=lambda **kwargs: {})
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
            service = SimpleNamespace(provider_id="example", begin=lambda **kwargs: {}, complete=lambda **kwargs: {})
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

    def test_runtime_readiness_reflects_component_health_and_shutdown(self):
        class Provider:
            def generate(self, prompt):
                return prompt
            def is_available(self):
                return True
            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory, "provider": "mock"},
                auth_service=auth, agent_loop=_loop(),
                component_registry_factory=lambda auth_service, agent_loop, application:
                    build_core_component_registry(
                        auth_service=auth_service, provider=Provider(),
                        agent_loop=agent_loop, application_runtime=application,
                    ),
            )
            self.assertEqual(
                runtime.component_registry.resolve("configuration-runtime").get("provider"),
                "mock",
            )
            self.assertTrue(runtime.component_registry.health()["configuration-runtime"])
            self.assertEqual(runtime.readiness_report().capabilities["component_runtime"].status, "verified")
            runtime.close()
            self.assertEqual(runtime.readiness_report().capabilities["component_runtime"].status, "unavailable")


if __name__ == "__main__":
    unittest.main()
