"""Provider execution boundary with durable idempotency correlation.

A provider adapter is trusted host code configured with a fixed provider ID and
an authenticated provider client. The model cannot choose either. The callback
must forward the supplied idempotency key to the provider's native idempotency
mechanism; this module never retries ambiguous operations.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import re
from typing import Any, Callable

from core.operation_context import OperationExecutionContext, current_operation_context


_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class ProviderOperationResult:
    provider_id: str
    operation_id: str
    action_digest: str
    result: Any


class ProviderOperationAdapter:
    """Bind one trusted provider callback to the active approved operation.

    execute_provider receives only host-validated request data plus immutable
    owner/operation context and the stable operation ID as idempotency_key.
    The callback must use the key in the provider's own idempotency facility.
    """

    def __init__(
        self,
        provider_id: str,
        execute_provider: Callable[..., Any],
        bind_provider: Callable[[OperationExecutionContext, str], Any],
    ) -> None:
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            raise ValueError("provider_id must be a normalized lowercase identifier")
        if not callable(execute_provider):
            raise TypeError("execute_provider must be callable")
        if not callable(bind_provider):
            raise TypeError("bind_provider must durably bind provider identity")
        self._provider_id = provider_id
        self._execute_provider = execute_provider
        self._bind_provider = bind_provider

    @property
    def provider_id(self) -> str:
        """The immutable provider identity configured by trusted host code."""
        return self._provider_id

    def __call__(self, request: Mapping[str, Any]) -> ProviderOperationResult:
        if not isinstance(request, Mapping):
            raise TypeError("provider request must be a mapping")
        try:
            normalized = json.loads(json.dumps(
                dict(request), ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            ))
        except (TypeError, ValueError) as exc:
            raise ValueError("provider request must be bounded JSON-compatible data") from exc
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 20_000:
            raise ValueError("provider request exceeds the 20 KiB limit")

        context = current_operation_context()
        if not isinstance(context, OperationExecutionContext):
            raise PermissionError("provider operation requires a durable approved execution context")

        # The operation ID is persisted before execution and remains stable
        # across process recovery. It is the provider's idempotency key, not a
        # freshly generated request ID. The callback must not silently ignore it.
        # Provider identity must be committed to the owner-scoped operation
        # record before the external side effect can begin.
        self._bind_provider(context, self._provider_id)
        result = self._execute_provider(
            request=normalized,
            idempotency_key=context.operation_id,
            operation_context=context,
        )
        return ProviderOperationResult(
            provider_id=self._provider_id,
            operation_id=context.operation_id,
            action_digest=context.action_digest,
            result=result,
        )
