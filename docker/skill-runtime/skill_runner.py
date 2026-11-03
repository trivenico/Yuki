#!/usr/bin/env python3
"""Minimal one-shot runner for the isolated Yuki skill container."""
from __future__ import annotations

import importlib
import json
import os
import sys
import traceback


def _write_response(fd: int, invocation_id: str, status: str, output: object) -> None:
    payload = json.dumps({
        "protocol_version": 1,
        "invocation_id": invocation_id,
        "status": status,
        "output": output,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(payload) > 16 * 1024 * 1024:
        payload = json.dumps({
            "protocol_version": 1, "invocation_id": invocation_id,
            "status": "failed", "output": {"error": "output limit exceeded"},
        }, separators=(",", ":")).encode("utf-8")
    os.write(fd, payload)


def main() -> int:
    # Preserve the protocol channel, then prevent ordinary skill prints from
    # corrupting it. The supervisor still treats all returned data as untrusted.
    protocol_fd = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)
    invocation_id = "unknown"
    try:
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("request exceeds protocol limit")
        request = json.loads(raw.decode("utf-8"))
        required = {
            "protocol_version", "invocation_id", "skill_id", "entrypoint",
            "input_schema_sha256", "output_schema_sha256", "arguments",
        }
        if not isinstance(request, dict) or set(request) != required:
            raise ValueError("invalid request envelope")
        if type(request["protocol_version"]) is not int or request["protocol_version"] != 1:
            raise ValueError("unsupported protocol version")
        invocation_id = request["invocation_id"]
        if not isinstance(invocation_id, str) or not invocation_id or len(invocation_id) > 512:
            raise ValueError("invalid invocation identifier")
        entrypoint = request["entrypoint"]
        if not isinstance(entrypoint, str) or entrypoint.count(":") != 1:
            raise ValueError("invalid entrypoint")
        module_name, function_name = entrypoint.split(":")
        if (not module_name or not function_name
                or any(not part.isidentifier() for part in module_name.split("."))
                or not function_name.isidentifier()):
            raise ValueError("invalid entrypoint")
        sys.path.insert(0, "/yuki/skill")
        module = importlib.import_module(module_name)
        function = getattr(module, function_name, None)
        if not callable(function):
            raise ValueError("skill entrypoint is not callable")
        output = function(request["arguments"])
        # Enforce JSON-only output before it leaves the isolated process.
        json.dumps(output, ensure_ascii=False, allow_nan=False)
        _write_response(protocol_fd, invocation_id, "succeeded", output)
        return 0
    except BaseException:
        # Never return exception messages, source paths, or tracebacks.
        try:
            _write_response(protocol_fd, invocation_id, "failed", {"error": "skill execution failed"})
        except BaseException:
            pass
        return 0
    finally:
        os.close(protocol_fd)


if __name__ == "__main__":
    raise SystemExit(main())
