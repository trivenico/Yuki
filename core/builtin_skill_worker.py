"""Allowlisted subprocess worker for low-risk built-in skills.

This module accepts one bounded JSON request on stdin and emits one JSON response.
It is not a sandbox: only invoke it through the controlled dispatcher, and never
register arbitrary module names supplied by a user.
"""
from __future__ import annotations

import json
import sys

from skills import TimeSkill

MAX_INPUT_BYTES = 65536


def handle(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"skill_id", "arguments"}:
        raise ValueError("Invalid worker request.")
    if payload["skill_id"] != "system.time.read":
        raise ValueError("Unsupported built-in skill.")
    arguments = payload["arguments"]
    if not isinstance(arguments, dict) or arguments:
        raise ValueError("Time skill accepts no arguments.")
    result = TimeSkill().execute("")
    if not result.ok or not isinstance(result.message, str):
        raise RuntimeError("Time skill failed.")
    output = {"local_time": result.message}
    return {"ok": True, "result": {"output": output}}


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        return 2
    try:
        payload = json.loads(raw.decode("utf-8"))
        response = handle(payload)
        encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > 8192:
            return 3
        sys.stdout.buffer.write(encoded)
        return 0
    except Exception:
        # Keep failure details off stdout/stderr; dispatcher reports a generic failure.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
