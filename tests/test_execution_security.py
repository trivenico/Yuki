import json
import sys
import unittest

from core.action_approvals import ActionApprovalStore, action_fingerprint
from core.manifest_task_agent import ManifestTaskAgent
from core.skill_dispatch import SkillDispatcher
from core.skill_manifest import SkillManifest
from core.subprocess_executor import SubprocessExecutor
from core.permissions import PermissionGate


class FixedProvider:
    def __init__(self, response):
        self.response = response

    def generate(self, prompt):
        return self.response


def make_dispatcher(output_limit=1024):
    manifest = SkillManifest.from_dict({
        "skill_id": "notes.write",
        "version": "1.0.0",
        "entrypoint": "worker.run",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 100}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"saved": {"type": "boolean"}},
            "required": ["saved"],
            "additionalProperties": False,
        },
        "capabilities": ["workspace.write"],
        "risk": "low",
        "timeout_seconds": 2,
        "max_output_bytes": output_limit,
    })
    dispatcher = SkillDispatcher(
        capability_grants={"workspace.write"},
        approval_store=ActionApprovalStore(),
        permission_gate=PermissionGate(approver=lambda request: True),
    )
    script = "import json,sys; json.load(sys.stdin); print(json.dumps({'saved':True}))"
    dispatcher.register_subprocess(
        manifest, [sys.executable, "-c", script],
        executor=SubprocessExecutor(timeout_seconds=2, max_output_bytes=output_limit),
    )
    return dispatcher


class ExecutionBoundaryTests(unittest.TestCase):
    def test_dispatch_denies_without_fresh_approval(self):
        dispatcher = make_dispatcher()
        with self.assertRaises(PermissionError):
            dispatcher.dispatch("notes.write", {"text": "hello"})

    def test_approval_is_bound_to_exact_arguments_and_consumed_once(self):
        dispatcher = make_dispatcher()
        store = dispatcher.approval_store
        original = {"skill_id": "notes.write", "arguments": {"text": "approved"}}
        store.approve(action_fingerprint(original))
        with self.assertRaises(PermissionError):
            dispatcher.dispatch("notes.write", {"text": "tampered"})
        result = dispatcher.dispatch("notes.write", {"text": "approved"})
        self.assertEqual(result, {"saved": True})
        with self.assertRaises(PermissionError):
            dispatcher.dispatch("notes.write", {"text": "approved"})

    def test_unknown_capability_is_denied_even_after_approval(self):
        dispatcher = make_dispatcher()
        dispatcher.capability_grants.clear()
        action = {"skill_id": "notes.write", "arguments": {"text": "hello"}}
        dispatcher.approval_store.approve(action_fingerprint(action))
        with self.assertRaises(PermissionError):
            dispatcher.dispatch("notes.write", action["arguments"])

    def test_input_schema_rejects_extra_fields(self):
        dispatcher = make_dispatcher()
        action = {"skill_id": "notes.write", "arguments": {"text": "hello", "path": "/tmp/x"}}
        dispatcher.approval_store.approve(action_fingerprint(action))
        with self.assertRaises(ValueError):
            dispatcher.dispatch("notes.write", action["arguments"])

    def test_task_agent_rejects_non_object_and_unsupported_fields(self):
        for response in ('[]', '{"type":"shell","skill_id":"x","arguments":{}}',
                         '{"type":"tool_call","skill_id":"x","arguments":{},"command":"rm -rf /"}'):
            agent = ManifestTaskAgent(FixedProvider(response), object(), object())
            with self.subTest(response=response), self.assertRaises(ValueError):
                agent.run("test")

    def test_task_agent_rejects_oversized_prompt(self):
        agent = ManifestTaskAgent(FixedProvider("{}"), object(), object())
        with self.assertRaises(ValueError):
            agent.run("x" * 20_001)

    def test_subprocess_output_limit_is_enforced(self):
        executor = SubprocessExecutor(timeout_seconds=2, max_output_bytes=64)
        with self.assertRaises(RuntimeError):
            executor.run([sys.executable, "-c", "print('x' * 1000)"],
                         timeout_seconds=2, max_output_bytes=64)

    def test_subprocess_timeout_kills_child(self):
        executor = SubprocessExecutor(timeout_seconds=1, max_output_bytes=64)
        with self.assertRaises(TimeoutError):
            executor.run([sys.executable, "-c", "import time; time.sleep(5)"],
                         timeout_seconds=1, max_output_bytes=64)


if __name__ == "__main__":
    unittest.main()
