"""The web entrypoint shares one firewall with provider startup and API runtime."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import run_web


class WebFirewallCompositionTests(unittest.TestCase):
    def test_web_startup_passes_one_firewall_to_provider_manager_and_api(self):
        policy = object()
        config = {"workspace": ""}
        with tempfile.TemporaryDirectory() as directory:
            config["workspace"] = str(Path(directory) / "workspace")
            with (
                patch.object(run_web, "load_config", return_value=config),
                patch.object(run_web, "build_network_firewall_from_config", return_value=policy),
                patch.object(run_web, "BuiltInAuthService"),
                patch.object(run_web, "create_provider") as create_provider,
                patch.object(run_web, "ProviderAgentAdapter") as adapter_factory,
                patch.object(run_web, "ProviderRuntimeManager") as manager_factory,
                patch.object(run_web, "GuardedToolRuntime"),
                patch.object(run_web, "StructuredToolProtocol"),
                patch.object(run_web, "AgentExecutionLoop"),
                patch.object(run_web, "DevicePairingService"),
                patch.object(run_web, "InferenceJobService"),
                patch.object(run_web, "SQLiteIncomeOpportunityStore"),
                patch.object(run_web, "build_conversation_runtime", return_value="runtime") as runtime_factory,
            ):
                adapter_factory.return_value.stream_model = MagicMock()
                result = run_web.create_app()

        self.assertEqual(result, "runtime")
        self.assertIs(create_provider.call_args.kwargs["firewall_policy"], policy)
        self.assertIs(manager_factory.call_args.kwargs["firewall_policy"], policy)
        self.assertIs(runtime_factory.call_args.kwargs["network_firewall"], policy)


    def test_web_startup_passes_explicit_prepared_oauth_and_mcp_runtime(self):
        policy = object()
        oauth_connections = object()
        disconnect_resolver = object()
        provider_registry = object()
        mcp_runtime = object()
        config = {"workspace": ""}
        with tempfile.TemporaryDirectory() as directory:
            config["workspace"] = str(Path(directory) / "workspace")
            with (
                patch.object(run_web, "load_config", return_value=config),
                patch.object(run_web, "build_network_firewall_from_config", return_value=policy),
                patch.object(run_web, "BuiltInAuthService"),
                patch.object(run_web, "create_provider"),
                patch.object(run_web, "ProviderAgentAdapter") as adapter_factory,
                patch.object(run_web, "ProviderRuntimeManager"),
                patch.object(run_web, "GuardedToolRuntime"),
                patch.object(run_web, "StructuredToolProtocol"),
                patch.object(run_web, "AgentExecutionLoop"),
                patch.object(run_web, "DevicePairingService"),
                patch.object(run_web, "InferenceJobService"),
                patch.object(run_web, "SQLiteIncomeOpportunityStore"),
                patch.object(run_web, "build_conversation_runtime", return_value="runtime") as runtime_factory,
            ):
                adapter_factory.return_value.stream_model = MagicMock()
                result = run_web.create_app(
                    oauth_connections=oauth_connections,
                    oauth_disconnect_service_for_provider=disconnect_resolver,
                    prepared_oauth_provider_registry=provider_registry,
                    mcp_runtime=mcp_runtime,
                )

        self.assertEqual(result, "runtime")
        kwargs = runtime_factory.call_args.kwargs
        self.assertIs(kwargs["oauth_connections"], oauth_connections)
        self.assertIs(kwargs["oauth_disconnect_service_for_provider"], disconnect_resolver)
        self.assertIs(kwargs["prepared_oauth_provider_registry"], provider_registry)
        self.assertIs(kwargs["mcp_runtime"], mcp_runtime)
        self.assertIs(kwargs["network_firewall"], policy)

    def test_web_startup_rejects_partial_oauth_runtime_configuration(self):
        with self.assertRaisesRegex(RuntimeError, "requires the connection registry"):
            run_web.create_app(oauth_connections=object())

    def test_web_startup_rejects_mcp_without_complete_oauth_runtime(self):
        with self.assertRaisesRegex(RuntimeError, "requires the complete OAuth runtime"):
            run_web.create_app(mcp_runtime=object())


if __name__ == "__main__":
    unittest.main()
