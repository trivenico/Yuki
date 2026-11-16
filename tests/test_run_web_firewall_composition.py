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


if __name__ == "__main__":
    unittest.main()
