import tempfile
import unittest
from pathlib import Path

from api.runtime import build_conversation_runtime
from api.oauth_authorization import AuthenticatedOAuthAuthorizationAPI
from api.oauth_connections import AuthenticatedOAuthConnectionAPI
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry
from core.oauth_provider_registry import OAuthProviderPresentation, PreparedOAuthProviderRegistry
from core.mcp_oauth_provider_bootstrap import PreparedOAuthProvider
from types import SimpleNamespace
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


def _loop():
    def model(**kwargs):
        return {"text": "Runtime ready", "tool_calls": {"tool_calls": []}}
    protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
    return AgentExecutionLoop(model, protocol)


class ConversationRuntimeTests(unittest.TestCase):
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

    def test_owner_execution_factory_receives_conversation_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            resolved_owners = []

            def factory(owner_id):
                resolved_owners.append(owner_id)
                return _loop()

            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(), agent_loop_for_owner=factory,
            )
            try:
                conversation = runtime.app.conversations.create(owner_id="authenticated-user-42")
                result = runtime.app.conversations.reply(
                    owner_id="authenticated-user-42",
                    conversation_id=conversation["conversation_id"],
                    user_text="hello", request_id="owner-loop-request-1",
                    expected_version=conversation["version"], tools=[],
                )
                self.assertEqual(resolved_owners, ["authenticated-user-42"])
                self.assertEqual(result["status"], "completed")
            finally:
                runtime.close()

    def test_invalid_owner_execution_result_does_not_persist_user_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(),
                agent_loop_for_owner=lambda owner_id: object(),
            )
            try:
                conversation = runtime.app.conversations.create(owner_id="authenticated-user-43")
                with self.assertRaisesRegex(TypeError, "must return an AgentExecutionLoop"):
                    runtime.app.conversations.reply(
                        owner_id="authenticated-user-43",
                        conversation_id=conversation["conversation_id"],
                        user_text="hello", request_id="invalid-owner-loop-1",
                        expected_version=conversation["version"], tools=[],
                    )
                self.assertEqual(runtime.app.conversations.history(
                    owner_id="authenticated-user-43",
                    conversation_id=conversation["conversation_id"],
                ), [])
            finally:
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


if __name__ == "__main__":
    unittest.main()
