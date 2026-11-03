"""Strict bounded JSON protocol primitives for the isolated skill runtime.

The protocol is data-only. It does not import skill code or launch processes.
Input/output schema validation remains a mandatory supervisor/runner integration
step before this protocol can be used to execute skills.
"""
from __future__ import annotations

import json
from typing import Any, Mapping


class SkillRuntimeProtocolError(ValueError):
    """Malformed, oversized, or mismatched skill runtime protocol message."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SkillRuntimeProtocolError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value: str):
    raise SkillRuntimeProtocolError("non-finite JSON numbers are forbidden")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SkillRuntimeProtocolError("message must contain finite JSON data") from exc


def parse_bounded_json(raw: bytes, *, max_bytes: int) -> Any:
    if not isinstance(raw, bytes) or isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise SkillRuntimeProtocolError("invalid bounded JSON input")
    if len(raw) > max_bytes:
        raise SkillRuntimeProtocolError("runtime protocol message exceeds byte limit")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except SkillRuntimeProtocolError:
        raise
    except (UnicodeError, ValueError, TypeError) as exc:
        raise SkillRuntimeProtocolError("runtime protocol message is invalid JSON") from exc


def parse_runtime_request(raw: bytes, *, max_bytes: int) -> dict[str, Any]:
    value = parse_bounded_json(raw, max_bytes=max_bytes)
    fields = {
        "protocol_version", "invocation_id", "skill_id", "entrypoint",
        "input_schema_sha256", "output_schema_sha256", "arguments",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise SkillRuntimeProtocolError("runtime request fields do not match protocol v1")
    if type(value["protocol_version"]) is not int or value["protocol_version"] != 1:
        raise SkillRuntimeProtocolError("unsupported runtime request protocol")
    for field in ("invocation_id", "skill_id", "entrypoint", "input_schema_sha256", "output_schema_sha256"):
        if not isinstance(value[field], str) or not value[field] or len(value[field]) > 512:
            raise SkillRuntimeProtocolError("runtime request contains an invalid identifier")
    for field in ("input_schema_sha256", "output_schema_sha256"):
        digest = value[field]
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise SkillRuntimeProtocolError("runtime request contains an invalid schema digest")
    return value


def build_runtime_response(*, invocation_id: str, status: str, output: Any, max_bytes: int) -> bytes:
    if not isinstance(invocation_id, str) or not invocation_id or len(invocation_id) > 512:
        raise SkillRuntimeProtocolError("invalid invocation identifier")
    if status not in {"succeeded", "failed"}:
        raise SkillRuntimeProtocolError("invalid runtime response status")
    encoded = canonical_json_bytes({
        "protocol_version": 1,
        "invocation_id": invocation_id,
        "status": status,
        "output": output,
    })
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1 or len(encoded) > max_bytes:
        raise SkillRuntimeProtocolError("runtime response exceeds byte limit")
    return encoded


def parse_runtime_response(raw: bytes, *, expected_invocation_id: str, max_bytes: int) -> dict[str, Any]:
    value = parse_bounded_json(raw, max_bytes=max_bytes)
    fields = {"protocol_version", "invocation_id", "status", "output"}
    if not isinstance(value, dict) or set(value) != fields:
        raise SkillRuntimeProtocolError("runtime response fields do not match protocol v1")
    if type(value["protocol_version"]) is not int or value["protocol_version"] != 1:
        raise SkillRuntimeProtocolError("unsupported runtime response protocol")
    if value["invocation_id"] != expected_invocation_id:
        raise SkillRuntimeProtocolError("runtime response invocation mismatch")
    if value["status"] not in {"succeeded", "failed"}:
        raise SkillRuntimeProtocolError("invalid runtime response status")
    return value
