import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from core.authenticated_runtime_inventory import AuthenticatedRuntimeInventoryProvider
from core.host_runtime_inventory import RuntimeInventoryError
from core.host_runtime_inventory_ipc import (
    HostRuntimeInventoryAgent,
    UnixSocketRuntimeInventoryProbe,
    load_private_hmac_key,
)
from core.sandbox_executor import SandboxExecutionProfile
from core.sandbox_policy import SandboxPolicy
from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget

KEY = b"yuki-host-agent-test-key-material-at-least-32-bytes"
NOW = 1_800_000_000


@unittest.skipUnless(hasattr(socket, "AF_UNIX") and hasattr(socket, "SO_PEERCRED"), "Linux Unix peer credentials required")
class UnixSocketRuntimeInventoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.socket_path = str(Path(self.directory.name) / "agent.sock")
        self.spec = AgentExecutionSpec(
            spec_id="ipc-spec", role="reviewer", instructions="Review safely.",
            capabilities=("coding",), budget=ExecutionBudget(
                max_runtime_seconds=30, max_memory_mb=128, max_tokens=100, max_steps=2,
            ),
        )
        self.profile = SandboxExecutionProfile(
            spec_id=self.spec.spec_id, image="python@sha256:" + "b" * 64,
            policy=SandboxPolicy(timeout_seconds=30, memory_mb=128, cpu_millis=250),
            command=("python", "--version"), workspace_resolver=lambda _: "/workspace",
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_unix_socket_probe_and_host_agent_produce_verified_inventory(self):
        uid = os.getuid()
        agent = HostRuntimeInventoryAgent(
            source_id="host-agent-test", hmac_key=KEY, expected_client_uid=uid,
            clock=lambda: NOW,
            inventory_collector=lambda *_: {
                "runtime_versions": {"Python": "3.12.2"},
                "installed_dependencies": {"Requests": "2.32.0"},
            },
        )
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.socket_path)
        os.chmod(self.socket_path, 0o600)
        listener.listen(1)

        def serve():
            connection, _ = listener.accept()
            try:
                agent.handle_connection(connection)
            finally:
                connection.close()
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        probe = UnixSocketRuntimeInventoryProbe(
            socket_path=self.socket_path, expected_agent_uid=uid,
        )
        used = set()

        def consume(source, nonce, expiry):
            key = (source, nonce)
            if key in used:
                return False
            used.add(key)
            return True

        provider = AuthenticatedRuntimeInventoryProvider(
            probe=probe, consume_nonce=consume, hmac_key=KEY,
            source_id="host-agent-test", clock=lambda: NOW,
        )
        result = provider(
            "task-1", "worker-1", "a" * 64, self.profile.image,
            {"Id": "a" * 64} if False else self.spec, self.profile,
        )
        self.assertEqual(result["runtime_versions"], {"python": "3.12.2"})
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_key_loader_requires_private_regular_file(self):
        key_path = Path(self.directory.name) / "agent.key"
        key_path.write_bytes(KEY)
        os.chmod(key_path, 0o600)
        self.assertEqual(load_private_hmac_key(str(key_path), expected_uid=os.getuid()), KEY)
        os.chmod(key_path, 0o640)
        with self.assertRaises(ValueError):
            load_private_hmac_key(str(key_path))

    def test_probe_rejects_non_absolute_socket_path(self):
        with self.assertRaises(ValueError):
            UnixSocketRuntimeInventoryProbe(socket_path="relative.sock", expected_agent_uid=os.getuid())


if __name__ == "__main__":
    unittest.main()
