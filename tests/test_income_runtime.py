import tempfile
import unittest
from pathlib import Path

from api.income import AuthenticatedIncomeOpportunityAPI
from api.runtime import build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.guarded_tool_runtime import GuardedToolRuntime
from core.income_opportunities import SQLiteIncomeOpportunityStore
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


def _loop():
    def model(**kwargs):
        return {"text": "Runtime ready", "tool_calls": {"tool_calls": []}}
    protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
    return AgentExecutionLoop(model, protocol)


class IncomeRuntimeTests(unittest.TestCase):
    def test_runtime_wires_persistent_income_store_when_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
            auth.bootstrap_admin("owner", "a-secure-owner-password")
            income = SQLiteIncomeOpportunityStore(str(root / "income.sqlite"))
            runtime = build_conversation_runtime(
                {"storage_backend": "sqlite", "workspace": directory},
                auth_service=auth, agent_loop=_loop(), income_opportunities=income,
            )
            self.assertIsInstance(runtime.app, AuthenticatedIncomeOpportunityAPI)
            self.assertIs(runtime.app.opportunities, income)
            runtime.close()

    def test_runtime_rejects_wrong_income_store_type(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(str(Path(directory) / "auth.sqlite"), password_iterations=100_000)
            with self.assertRaisesRegex(TypeError, "income_opportunities"):
                build_conversation_runtime(
                    {"storage_backend": "sqlite", "workspace": directory},
                    auth_service=auth, agent_loop=_loop(), income_opportunities=object(),
                )


if __name__ == "__main__":
    unittest.main()
