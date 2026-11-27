"""Immutable, process-local identity for one exact guarded-tool invocation.

The envelope deliberately contains a digest rather than raw arguments. It is
authorization context, not a sandbox or a durable approval token.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import marshal
from typing import Callable


@dataclass(frozen=True, slots=True)
class ToolActionEnvelope:
    tool_name: str
    argument_sha256: str
    argument_bytes: int
    implementation_sha256: str
    owner_id: str | None
    session_id: str | None


def implementation_identity(handler: Callable) -> str:
    """Fingerprint the registered callable implementation for this Python runtime."""
    code = getattr(handler, "__code__", None)
    if code is None:
        call = getattr(handler, "__call__", None)
        code = getattr(call, "__code__", None)
    if code is None:
        raise TypeError("tool handler must expose a Python code object")
    module = getattr(handler, "__module__", type(handler).__module__)
    qualified = getattr(handler, "__qualname__", type(handler).__qualname__)
    material = module.encode("utf-8") + b"\0" + qualified.encode("utf-8") + b"\0" + marshal.dumps(code)
    return hashlib.sha256(material).hexdigest()


def build_tool_action(
    tool_name: str,
    argument: str,
    handler: Callable,
    *,
    owner_id: str | None,
    session_id: str | None,
) -> ToolActionEnvelope:
    encoded = argument.encode("utf-8")
    return ToolActionEnvelope(
        tool_name=tool_name,
        argument_sha256=hashlib.sha256(encoded).hexdigest(),
        argument_bytes=len(encoded),
        implementation_sha256=implementation_identity(handler),
        owner_id=owner_id,
        session_id=session_id,
    )
