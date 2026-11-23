import unittest
from dataclasses import replace

from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
from core.authenticated_runtime_inventory import (
    AuthenticatedRuntimeInventoryProvider,
    sign_runtime_inventory_envelope,
)
from core.host_runtime_inventory import RuntimeInventoryError
from core.sandbox_executor import SandboxExecutionProfile
from core.sandbox_policy import SandboxPolicy


KEY = b"yuki-test-host-agent-key-material-32-bytes-minimum"
NOW = 1_800_000_000
CONTAINER = "a" * 64
IMAGE = "python@sha256:" + "b" * 64


class AuthenticatedRuntimeInventoryProviderTests(unittest.TestCase):
    def setUp(self):
        self.spec = AgentExecutionSpec(
            spec_id="signed-inventory", role="reviewer", instructions="Inspect safely.",
            capabilities=("coding",), budget=ExecutionBudget(
                max_runtime_seconds=30, max_memory_mb=128, max_tokens=100, max_steps=2,
            ),
        )
        self.profile = SandboxExecutionProfile(
            spec_id=self.spec.spec_id, image=IMAGE,
            policy=SandboxPolicy(timeout_seconds=30, memory_mb=128, cpu_millis=250),
            command=("python", "--version"), workspace_resolver=lambda _: "/workspace",
        )
        self.used_nonces = set()
        self.envelope = {
            "source_id": "yuki-host-agent-1",
            "task_id": "task-1",
            "worker_id": "worker-1",
            "container_id": CONTAINER,
            "image_ref": IMAGE,
            "spec_id": self.spec.spec_id,
            "issued_at": NOW,
            "expires_at": NOW + 30,
            "nonce": "abcdefghijklmnopqrstuvwx",
            "runtime_versions": {"Python": "3.12.2"},
            "installed_dependencies": {"Requests": "2.32.0"},
        }
        self.provider = self.make_provider()

    def make_provider(self, *, probe=None, clock=lambda: NOW):
        def consume(source, nonce, expiry):
            key = (source, nonce)
            if key in self.used_nonces:
                return False
            self.used_nonces.add(key)
            return True
        def get_envelope(*_):
            unsigned = dict(self.envelope)
            return {**unsigned, "signature": sign_runtime_inventory_envelope(unsigned, KEY)}
        return AuthenticatedRuntimeInventoryProvider(
            probe=probe or get_envelope, consume_nonce=consume, hmac_key=KEY,
            source_id="yuki-host-agent-1", clock=clock,
            maximum_age_seconds=60,
        )

    def collect(self, provider=None):
        return (provider or self.provider)(
            "task-1", "worker-1", CONTAINER, {"Id": CONTAINER},
            self.spec, self.profile,
        )

    def test_verifies_signature_and_binds_inventory_to_container_task_worker_image(self):
        result = self.collect()
        self.assertEqual(result["runtime_versions"], {"python": "3.12.2"})
        self.assertEqual(result["installed_dependencies"], {"requests": "2.32.0"})

    def test_rejects_tampered_signature_and_wrong_execution_identity(self):
        def tampered(*_):
            unsigned = dict(self.envelope)
            signed = {**unsigned, "signature": sign_runtime_inventory_envelope(unsigned, KEY)}
            signed["worker_id"] = "other-worker"
            return signed
        with self.assertRaises(RuntimeInventoryError):
            self.collect(self.make_provider(probe=tampered))
        def wrong_image(*_):
            unsigned = dict(self.envelope)
            unsigned["image_ref"] = "python:latest"
            return {**unsigned, "signature": sign_runtime_inventory_envelope(unsigned, KEY)}
        with self.assertRaises(RuntimeInventoryError):
            self.collect(self.make_provider(probe=wrong_image))

    def test_rejects_expired_and_replayed_envelopes(self):
        with self.assertRaises(RuntimeInventoryError):
            self.collect(self.make_provider(clock=lambda: NOW + 31))
        self.assertEqual(self.collect(), {
            "runtime_versions": {"python": "3.12.2"},
            "installed_dependencies": {"requests": "2.32.0"},
        })
        with self.assertRaises(RuntimeInventoryError):
            self.collect()

    def test_rejects_bad_nonce_and_unbounded_inventory(self):
        self.envelope["nonce"] = "short"
        with self.assertRaises(RuntimeInventoryError):
            self.collect()
        self.envelope["nonce"] = "zyxwvutsrqponmlkjihgfedc"
        self.envelope["runtime_versions"] = {"python": "3.12.2", "PYTHON": "3.13"}
        with self.assertRaises(RuntimeInventoryError):
            self.collect()

    def test_requires_operator_key_and_durable_replay_guard(self):
        with self.assertRaises(ValueError):
            AuthenticatedRuntimeInventoryProvider(
                probe=lambda *_: {}, consume_nonce=lambda *_: True,
                hmac_key=b"short", source_id="yuki-host-agent-1",
            )
        provider = AuthenticatedRuntimeInventoryProvider(
            probe=lambda *_: {}, consume_nonce=lambda *_: None,
            hmac_key=KEY, source_id="yuki-host-agent-1",
            clock=lambda: NOW,
        )
        with self.assertRaises(RuntimeInventoryError):
            self.collect(provider)


if __name__ == "__main__":
    unittest.main()
