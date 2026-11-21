"""Trusted orchestration boundary for bounded code execution.

The service does not create a sandbox. It verifies a grant, requires audit
availability, constrains every host-side tool RPC, and validates untrusted output.
"""
from __future__ import annotations

import json
from typing import Callable, Mapping

from core.code_execution import (
    CancellationCheck,
    CodeExecutionContractError,
    CodeExecutionRequest,
    CodeExecutionResult,
    CodeExecutor,
    ExecutionGrant,
    ExecutionGrantVerifier,
    ToolDispatcher,
)

AuditRecorder = Callable[[str, Mapping[str, object]], None]
_MAX_TOOL_ARGUMENT_BYTES = 32_768
_MAX_TOOL_RESULT_BYTES = 65_536


class CodeExecutionService:
    """Control-plane wrapper around a replaceable, untrusted execution backend.

    The supplied dispatcher must itself enforce Yuki's account ownership,
    approvals, idempotency, and tool-specific policy. This wrapper adds a
    second exact-request allow-list and mandatory execution audit events.
    """

    def __init__(
        self,
        executor: CodeExecutor,
        grant_verifier: ExecutionGrantVerifier,
        *,
        audit: AuditRecorder,
    ) -> None:
        if not callable(getattr(executor, "execute", None)):
            raise TypeError("executor must implement execute()")
        if not callable(getattr(grant_verifier, "verify", None)) or not callable(
            getattr(grant_verifier, "authorize_tool_call", None)
        ):
            raise TypeError("grant_verifier must implement verify() and authorize_tool_call()")
        if not callable(audit):
            raise TypeError("audit recorder is required")
        self._executor = executor
        self._grant_verifier = grant_verifier
        self._audit = audit

    def _record(self, event: str, request: CodeExecutionRequest, **details: object) -> None:
        record: dict[str, object] = {
            "actor_id": request.actor_id,
            "task_id": request.task_id,
            "execution_id": request.execution_id,
            "manifest_sha256": request.manifest_sha256,
        }
        record.update(details)
        try:
            self._audit(event, record)
        except Exception as exc:
            raise CodeExecutionContractError("mandatory execution audit failed") from exc

    def execute(
        self,
        request: CodeExecutionRequest,
        grant: ExecutionGrant,
        *,
        dispatch_tool: ToolDispatcher,
        cancellation_requested: CancellationCheck,
    ) -> CodeExecutionResult:
        request.validate()
        if not callable(dispatch_tool) or not callable(cancellation_requested):
            raise TypeError("tool dispatcher and cancellation callback are required")
        grant.validate_for(request)
        try:
            verified = self._grant_verifier.verify(grant, request)
        except Exception as exc:
            raise CodeExecutionContractError("execution grant verification failed") from exc
        if verified is not True:
            raise CodeExecutionContractError("execution grant is not trusted")

        try:
            initially_cancelled = cancellation_requested()
        except Exception as exc:
            raise CodeExecutionContractError("cancellation state could not be verified before execution") from exc
        if initially_cancelled is not False:
            raise CodeExecutionContractError("execution is cancelled or cancellation state is unknown")
        self._record("code_execution.started", request)
        allowed = frozenset(request.tool_names)
        call_count = 0

        def guarded_dispatch(name: str, arguments: Mapping[str, object]) -> Mapping[str, object]:
            nonlocal call_count
            if not isinstance(name, str) or name not in allowed:
                raise CodeExecutionContractError("tool is not present in the approved execution manifest")
            if call_count >= request.max_tool_calls:
                raise CodeExecutionContractError("execution exceeded its tool-call budget")
            if not isinstance(arguments, Mapping):
                raise CodeExecutionContractError("tool arguments must be a mapping")
            try:
                argument_bytes = json.dumps(
                    dict(arguments), sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeError) as exc:
                raise CodeExecutionContractError("tool arguments are not bounded JSON data") from exc
            if len(argument_bytes) > _MAX_TOOL_ARGUMENT_BYTES:
                raise CodeExecutionContractError("tool arguments exceed the host RPC limit")
            try:
                cancelled = cancellation_requested()
            except Exception as exc:
                raise CodeExecutionContractError("cancellation state could not be verified") from exc
            if cancelled is not False:
                raise CodeExecutionContractError("execution is cancelled or cancellation state is unknown")

            self._record("code_execution.tool_requested", request, tool_name=name)
            call_count += 1

            def perform_host_operation() -> Mapping[str, object]:
                # Recheck immediately inside the verifier's atomic authorization
                # section so cancellation cannot slip between the outer check
                # and the host-side side effect.
                try:
                    still_active = cancellation_requested()
                except Exception as exc:
                    raise CodeExecutionContractError("cancellation state could not be verified at dispatch") from exc
                if still_active is not False:
                    raise CodeExecutionContractError("execution was cancelled before host dispatch")
                result = dispatch_tool(name, dict(arguments))
                if not isinstance(result, Mapping):
                    raise CodeExecutionContractError("host tool returned a non-mapping result")
                try:
                    result_bytes = json.dumps(
                        dict(result), sort_keys=True, separators=(",", ":"), allow_nan=False
                    ).encode("utf-8")
                except (TypeError, ValueError, UnicodeError) as exc:
                    raise CodeExecutionContractError("host tool result is not bounded JSON data") from exc
                if len(result_bytes) > _MAX_TOOL_RESULT_BYTES:
                    raise CodeExecutionContractError("host tool result exceeds the RPC limit")
                return dict(result)

            try:
                result = self._grant_verifier.authorize_tool_call(
                    grant, request, tool_name=name, operation=perform_host_operation
                )
            except CodeExecutionContractError:
                raise
            except Exception as exc:
                raise CodeExecutionContractError("atomic grant authorization for tool RPC failed") from exc
            if not isinstance(result, Mapping):
                raise CodeExecutionContractError("grant verifier returned an invalid tool result")
            self._record("code_execution.tool_completed", request, tool_name=name)
            return dict(result)

        try:
            result = self._executor.execute(
                request,
                grant,
                dispatch_tool=guarded_dispatch,
                cancellation_requested=cancellation_requested,
            )
            if not isinstance(result, CodeExecutionResult):
                raise CodeExecutionContractError("executor returned an invalid result envelope")
            result.validate(request)
            if result.tool_calls != call_count:
                raise CodeExecutionContractError("executor tool-call count does not match host-dispatched calls")
            try:
                final_authorized = self._grant_verifier.verify(grant, request)
                cancelled_at_completion = cancellation_requested()
            except Exception as exc:
                raise CodeExecutionContractError("execution authorization or cancellation state is unknown") from exc
            if final_authorized is not True:
                raise CodeExecutionContractError("execution grant is no longer trusted at completion")
            if cancelled_at_completion is not False and not (
                cancelled_at_completion is True and result.status == "cancelled"
            ):
                raise CodeExecutionContractError("execution completed with an inconsistent cancellation state")
            self._record(
                "code_execution.completed",
                request,
                status=result.status,
                tool_calls=call_count,
            )
            return result
        except Exception as exc:
            # If this mandatory failure event cannot be persisted, _record raises
            # and intentionally replaces the original error: audit availability
            # is a prerequisite for accepting an execution outcome.
            self._record(
                "code_execution.failed",
                request,
                error_type=type(exc).__name__,
                tool_calls=call_count,
            )
            if isinstance(exc, CodeExecutionContractError):
                raise
            raise CodeExecutionContractError("code execution failed") from exc
