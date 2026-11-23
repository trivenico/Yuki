import base64
import json
import os
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from core.execution_gate_transport import (
    ExecutionGateAuthorizationListener,
    ExecutionGateTransportError,
    receive_gate_authorization,
)


class ExecutionGateTransportTests(unittest.TestCase):
    def test_receiver_checks_host_peer_uid_and_reads_bounded_frame(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        key = b"k" * 32
        payload = json.dumps({
            "protocol": "yuki.execution-gate.transport.v1",
            "token": "signed-token",
            "key": base64.b64encode(key).decode("ascii"),
        }, separators=(",", ":")).encode("ascii")
        right.sendall(struct.pack("!I", len(payload)) + payload)
        token, received_key = receive_gate_authorization(
            left, expected_host_uid=os.getuid(),
        )
        self.assertEqual(token, "signed-token")
        self.assertEqual(received_key, key)
        self.assertEqual(right.recv(1), b"\x06")

    def test_receiver_rejects_wrong_peer_uid(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        with self.assertRaises(ExecutionGateTransportError):
            receive_gate_authorization(left, expected_host_uid=os.getuid() + 1)

    def test_listener_requires_private_service_owned_parent(self):
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "gate.sock"
            listener = ExecutionGateAuthorizationListener(
                socket_path=str(path), expected_gate_pid=os.getpid(),
                expected_gate_uid=os.getuid(),
            )
            self.assertFalse(path.exists())
            with listener:
                self.assertTrue(path.exists())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(path.exists())

    def test_listener_requires_post_create_gate_identity_before_delivery(self):
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root) / "private"
            parent.mkdir(mode=0o700)
            listener = ExecutionGateAuthorizationListener(
                socket_path=str(parent / "gate.sock"), timeout_seconds=1,
            )
            with listener:
                with self.assertRaisesRegex(ExecutionGateTransportError, "identity has not been bound"):
                    listener.deliver(token="signed-token", key=b"x" * 32)
                listener.bind_gate_identity(gate_pid=os.getpid(), gate_uid=os.getuid())
                with self.assertRaisesRegex(ExecutionGateTransportError, "immutable"):
                    listener.bind_gate_identity(gate_pid=os.getpid(), gate_uid=os.getuid())

    def test_listener_rejects_invalid_inspected_identity(self):
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root) / "private"
            parent.mkdir(mode=0o700)
            listener = ExecutionGateAuthorizationListener(
                socket_path=str(parent / "gate.sock"),
            )
            with listener:
                with self.assertRaises(ValueError):
                    listener.bind_gate_identity(gate_pid=0, gate_uid=os.getuid())
                with self.assertRaises(ExecutionGateTransportError):
                    listener.deliver(token="signed-token", key=b"x" * 32)

    def test_listener_rejects_existing_socket_path(self):
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "gate.sock"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(path))
            sock.close()
            with self.assertRaises(ValueError):
                ExecutionGateAuthorizationListener(
                    socket_path=str(path), expected_gate_pid=os.getpid(),
                    expected_gate_uid=os.getuid(),
                )

    def test_listener_delivers_once_only_to_expected_process(self):
        if not hasattr(socket, "SO_PEERCRED"):
            self.skipTest("Linux peer credentials are unavailable")
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "gate.sock"
            ready = threading.Event()
            result = {}

            def gate_client():
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.connect(str(path))
                ready.set()
                header = client.recv(4)
                length = struct.unpack("!I", header)[0]
                raw = bytearray()
                while len(raw) < length:
                    raw.extend(client.recv(length - len(raw)))
                result.update(json.loads(raw.decode("ascii")))
                client.sendall(b"\x06")
                client.close()

            listener = ExecutionGateAuthorizationListener(
                socket_path=str(path), expected_gate_pid=os.getpid(),
                expected_gate_uid=os.getuid(), timeout_seconds=3,
            )
            listener.open()
            thread = threading.Thread(target=gate_client)
            thread.start()
            listener.deliver(token="signed-token", key=b"x" * 32)
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result["token"], "signed-token")
            self.assertEqual(base64.b64decode(result["key"]), b"x" * 32)
            with self.assertRaises(ExecutionGateTransportError):
                listener.deliver(token="again", key=b"x" * 32)

    def test_listener_rejects_public_parent_directory(self):
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root) / "public"
            parent.mkdir(mode=0o755)
            with self.assertRaisesRegex(ValueError, "private"):
                ExecutionGateAuthorizationListener(
                    socket_path=str(parent / "gate.sock"),
                    expected_gate_pid=os.getpid(), expected_gate_uid=os.getuid(),
                )


if __name__ == "__main__":
    unittest.main()
