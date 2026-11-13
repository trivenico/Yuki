"""Trusted, task-local context for one approved external operation.

The context is created only by the approval coordinator after durable intent
persistence. Provider adapters may read it to attach a stable idempotency key
and correlate independently verified provider receipts. It is never model input.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
import re
from typing import Iterator


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CURRENT_OPERATION: ContextVar["OperationExecutionContext | None"] = ContextVar(
    "yuki_operation_execution_context", default=None
)


@dataclass(frozen=True)
class OperationExecutionContext:
    pending_id: str
    operation_id: str
    action_digest: str
    actor: str
    session_id: str

    def __post_init__(self) -> None:
        for name in ("pending_id", "operation_id", "actor", "session_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{name} must be non-empty bounded text")
        if not isinstance(self.action_digest, str) or not _SHA256.fullmatch(self.action_digest):
            raise ValueError("action_digest must be a lowercase SHA-256 digest")


def current_operation_context() -> OperationExecutionContext | None:
    """Return the operation context for this execution task, if one is active."""
    return _CURRENT_OPERATION.get()


@contextmanager
def bind_operation_context(
    context: OperationExecutionContext,
) -> Iterator[OperationExecutionContext]:
    """Bind a trusted operation context and always restore the prior context."""
    if not isinstance(context, OperationExecutionContext):
        raise TypeError("context must be an OperationExecutionContext")
    token: Token = _CURRENT_OPERATION.set(context)
    try:
        yield context
    finally:
        _CURRENT_OPERATION.reset(token)
