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
    ExecutionGateAuthorizationListener, ExecutionGateTransportError,
    acknowledge_gate_authorization, receive_gate_authorization,
)


class ExecutionGateTransportTests(unittest.TestCase):
    def test_receiver_defers_ack_until_explicit_gate_verification(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        key = b"k" * 32
        payload = json.dumps({
            "protocol": "yuki.execution-gate.transport.v1",
            "token": "signed-token", "key": base64.b64encode(key).decode("ascii"),
        }, separators=(",", ":")).encode("ascii")
        right.sendall(struct.pack("!I", len(payload)) + payload)
        token, received_key = receive_gate_authorization(left, expected_host_uid=os.getuid())
        self.assertEqual((token, received_key), ("signed-token", key))
        right.settimeout(0.05)
        with self.assertRaises(socket.timeout):
            right.recv(1)
        acknowledge_gate_authorization(left)
        self.assertEqual(right.recv(1), b"\x06")

    def test_receiver_rejects_wrong_peer_uid(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        with self.assertRaises(ExecutionGateTransportError):
            receive_gate_authorization(left, expected_host_uid=os.getuid() + 1)

    def test_listener_binds_identity_once_and_delivers_once(self):
        if not hasattr(socket, "SO_PEERCRED"):
            self.skipTest("Linux peer credentials unavailable")
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "gate.sock"
            listener = ExecutionGateAuthorizationListener(socket_path=str(path), timeout_seconds=2)
            listener.open()
            result = {}
            def client():
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.connect(str(path))
                header = connection.recv(4)
                length = struct.unpack("!I", header)[0]
                raw = bytearray()
                while len(raw) < length:
                    raw.extend(connection.recv(length - len(raw)))
                result.update(json.loads(raw.decode("ascii")))
                connection.sendall(b"\x06")
                connection.close()
            thread = threading.Thread(target=client)
            thread.start()
            listener.bind_gate_identity(gate_pid=os.getpid(), gate_uid=os.getuid())
            listener.deliver(token="signed-token", key=b"x" * 32)
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result["token"], "signed-token")
            self.assertEqual(base64.b64decode(result["key"]), b"x" * 32)
            with self.assertRaises(ExecutionGateTransportError):
                listener.deliver(token="replay", key=b"x" * 32)

    def test_listener_requires_private_parent_and_bound_identity(self):
        with tempfile.TemporaryDirectory() as root:
            public = Path(root) / "public"
            public.mkdir(mode=0o755)
            with self.assertRaisesRegex(ValueError, "private"):
                ExecutionGateAuthorizationListener(socket_path=str(public / "gate.sock"))
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            listener = ExecutionGateAuthorizationListener(socket_path=str(private / "gate.sock"))
            with listener:
                with self.assertRaisesRegex(ExecutionGateTransportError, "identity has not been bound"):
                    listener.deliver(token="token", key=b"x" * 32)


if __name__ == "__main__":
    unittest.main()
