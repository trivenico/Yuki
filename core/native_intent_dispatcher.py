"""Authorized dispatch boundary for parsed native intents.

Parsing is not authorization. This dispatcher requires an authenticated actor,
a per-intent authorization decision, explicit confirmation for mutating intents,
and a durable audit callback before invoking a registered handler.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from core.native_intents import IntentKind, NativeIntent, NativeIntentParser


class NativeDispatchDenied(PermissionError):
    """Raised when an intent is not authorized or lacks confirmation."""


class NativeDispatchAuditError(RuntimeError):
    """Raised when the required pre-execution audit record cannot be written."""


@dataclass(frozen=True)
class NativeDispatchResult:
    kind: IntentKind
    output: str
    executed: bool


class NativeIntentDispatcher:
    """Dispatch only registered intent kinds after auth, confirmation and audit."""

    def __init__(
        self,
        parser: NativeIntentParser,
        handlers: Mapping[IntentKind, Callable[[str, tuple[str, ...]], Any]],
        *,
        authorize: Callable[[str, IntentKind], bool],
        audit: Callable[[dict[str, Any]], None],
    ) -> None:
        if not isinstance(parser, NativeIntentParser):
            raise TypeError("parser must be a NativeIntentParser.")
        if not isinstance(handlers, Mapping):
            raise TypeError("handlers must be a mapping.")
        normalized: dict[IntentKind, Callable[[str, tuple[str, ...]], Any]] = {}
        for kind, handler in handlers.items():
            if not isinstance(kind, IntentKind) or kind is IntentKind.UNKNOWN:
                raise ValueError("handlers may only register supported intent kinds.")
            if not callable(handler):
                raise TypeError("every intent handler must be callable.")
            normalized[kind] = handler
        if not callable(authorize):
            raise TypeError("authorize must be callable.")
        if not callable(audit):
            raise TypeError("audit must be callable; native dispatch is audit-required.")
        self._parser = parser
        self._handlers = normalized
        self._authorize = authorize
        self._audit = audit

    def dispatch(
        self,
        actor_id: str,
        text: str,
        *,
        confirmed: bool = False,
    ) -> NativeDispatchResult:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise ValueError("actor_id must be non-empty bounded text.")
        if type(confirmed) is not bool:
            raise TypeError("confirmed must be a boolean.")

        intent = self._parser.parse(text)
        if intent.kind is IntentKind.UNKNOWN:
            return NativeDispatchResult(intent.kind, "I couldn't match that to a supported command.", False)
        handler = self._handlers.get(intent.kind)
        if handler is None:
            return NativeDispatchResult(intent.kind, "That command is not available.", False)
        if intent.requires_confirmation and not confirmed:
            raise NativeDispatchDenied("This action requires explicit confirmation.")
        try:
            authorized = self._authorize(actor_id.strip(), intent.kind) is True
        except Exception:
            authorized = False
        if not authorized:
            raise NativeDispatchDenied("This command is not authorized.")

        # Audit before execution: a failed audit must never silently permit a
        # potentially sensitive read or mutation to proceed.
        event = {
            "event": "native_intent.dispatch",
            "actor_id": actor_id.strip(),
            "intent": intent.kind.value,
            "argument_count": len(intent.arguments),
            "confirmed": confirmed,
        }
        try:
            self._audit(event)
        except Exception as exc:
            raise NativeDispatchAuditError("Required native dispatch audit failed.") from exc

        output = handler(actor_id.strip(), intent.arguments)
        if not isinstance(output, str):
            output = str(output)
        if len(output) > 8_000:
            output = output[:8_000] + "… [truncated]"
        return NativeDispatchResult(intent.kind, output, True)


__all__ = [
    "NativeDispatchAuditError",
    "NativeDispatchDenied",
    "NativeDispatchResult",
    "NativeIntentDispatcher",
]
