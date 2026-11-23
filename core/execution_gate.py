"""Image-owned PID 1 execution gate.

The image must configure this module as its fixed entrypoint. It accepts no task
argv from the image or environment: the only command it can execute is the
authenticated argv delivered over the private host channel.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

from core.execution_gate_protocol import (
    ExecutionGateExpectedIdentity,
    ExecutionGateReplayGuard,
    consume_execution_gate_authorization,
)
from core.execution_gate_transport import (
    ExecutionGateTransportError,
    receive_gate_authorization,
)


GATE_SOCKET_PATH = "/run/yuki-gate/authorization.sock"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yuki-execution-gate")
    parser.add_argument("--socket", default=GATE_SOCKET_PATH)
    parser.add_argument("--host-uid", required=True, type=int)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--spec-id", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def _receive(path: str, *, timeout: float, expected_host_uid: int) -> tuple[str, bytearray]:
    if not hasattr(socket, "SO_PEERCRED"):
        raise ExecutionGateTransportError("Linux peer credentials are required")
    if type(path) is not str or not os.path.isabs(path):
        raise ValueError("gate socket path must be absolute")
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(min(2.0, max(0.1, deadline - time.monotonic())))
        try:
            connection.connect(path)
            with connection:
                token, key = receive_gate_authorization(
                    connection, expected_host_uid=expected_host_uid,
                )
            return token, bytearray(key)
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError) as exc:
            last_error = exc
            connection.close()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        except Exception:
            connection.close()
            raise
    raise ExecutionGateTransportError("timed out waiting for host authorization socket") from last_error


def run_gate(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 0.1 <= args.timeout <= 120:
        raise ValueError("gate timeout must be between 0.1 and 120 seconds")
    if args.socket != GATE_SOCKET_PATH:
        raise ValueError("gate socket path must use the image-owned private runtime location")
    if args.host_uid <= 0:
        raise ValueError("expected host UID must be a non-root service identity")
    binding = ExecutionGateExpectedIdentity(
        task_id=args.task_id,
        worker_id=args.worker_id,
        spec_id=args.spec_id,
        image=args.image,
    )
    token, key_buffer = _receive(
        args.socket, timeout=args.timeout, expected_host_uid=args.host_uid,
    )
    try:
        authorization = consume_execution_gate_authorization(
            token=token,
            key=bytes(key_buffer),
            expected=binding,
            replay_guard=ExecutionGateReplayGuard(maximum_entries=1),
        )
    finally:
        for index in range(len(key_buffer)):
            key_buffer[index] = 0
        del key_buffer

    # The gate has no fallback command. Close all authorization state before exec.
    command = authorization.command
    os.execvpe(command[0], list(command), os.environ.copy())
    return 127


def main() -> None:
    try:
        result = run_gate()
    except Exception as exc:
        print(f"yuki execution gate: authorization failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(126) from None
    raise SystemExit(result)


if __name__ == "__main__":
    main()
