import tempfile
import unittest
from pathlib import Path
from api.runtime import build_conversation_runtime
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.device_pairing import DevicePairingService
from core.inference_jobs import InferenceJobService
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol

class NoApprovals:
    def authorize_tool(self, name): return False

def make_loop():
    def model(**kwargs): return {"text":"ready","tool_calls":{"tool_calls":[]}}
    return AgentExecutionLoop(model,StructuredToolProtocol(GuardedToolRuntime({},NoApprovals())))

class InferenceRuntimeWiringTests(unittest.TestCase):
    def test_runtime_injects_shared_pairing_job_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            auth=BuiltInAuthService(str(root/"auth.sqlite"),password_iterations=100_000)
            pairing=DevicePairingService(root/"pairing.sqlite")
            jobs=InferenceJobService(root/"jobs.sqlite",pairing)
            runtime=build_conversation_runtime(
                {"storage_backend":"sqlite","workspace":directory},
                auth_service=auth,agent_loop=make_loop(),inference_jobs=jobs)
            self.assertIs(runtime.app.inference_jobs,jobs)
            runtime.close()

    def test_invalid_job_service_rejected_before_store_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            auth=BuiltInAuthService(str(root/"auth.sqlite"),password_iterations=100_000)
            with self.assertRaisesRegex(TypeError,"inference_jobs"):
                build_conversation_runtime(
                    {"storage_backend":"sqlite","workspace":directory},
                    auth_service=auth,agent_loop=make_loop(),inference_jobs=object())
            self.assertFalse((root/"conversations.sqlite").exists())

if __name__=="__main__": unittest.main()
