"""Content-addressed, offline JSON Schema validation for skill I/O."""
from __future__ import annotations
import hashlib
import json
from typing import Any

MAX_SCHEMA_BYTES = 262_144


class SkillSchemaError(ValueError):
    """A schema artifact or instance failed strict validation."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SkillSchemaError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_remote_refs(value: Any) -> None:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if ref is not None and (not isinstance(ref, str) or not ref.startswith("#")):
            raise SkillSchemaError("remote JSON Schema references are forbidden")
        for child in value.values():
            _reject_remote_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_remote_refs(child)


def load_verified_schema(raw: bytes, expected_sha256: str) -> dict[str, Any]:
    if (not isinstance(raw, bytes) or not raw or len(raw) > MAX_SCHEMA_BYTES
            or not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in expected_sha256)):
        raise SkillSchemaError("schema artifact or digest is invalid")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise SkillSchemaError("schema artifact digest mismatch")
    try:
        schema = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=lambda _v: (_ for _ in ()).throw(
                SkillSchemaError("non-finite schema numbers are forbidden")
            ),
        )
    except SkillSchemaError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise SkillSchemaError("schema artifact is invalid JSON") from exc
    if not isinstance(schema, dict):
        raise SkillSchemaError("JSON Schema root must be an object")
    _reject_remote_refs(schema)
    try:
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(schema)
    except ImportError as exc:
        raise SkillSchemaError("JSON Schema validator dependency is unavailable") from exc
    except Exception as exc:
        raise SkillSchemaError("JSON Schema definition is invalid") from exc
    return schema


def validate_instance(instance: Any, schema: dict[str, Any]) -> None:
    if not isinstance(schema, dict):
        raise SkillSchemaError("verified JSON Schema object required")
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        error = next(validator.iter_errors(instance), None)
    except ImportError as exc:
        raise SkillSchemaError("JSON Schema validator dependency is unavailable") from exc
    except Exception as exc:
        raise SkillSchemaError("JSON Schema validation could not be completed") from exc
    if error is not None:
        raise SkillSchemaError("instance does not satisfy the approved JSON Schema")
