"""Guarded recovery coordinator for Yuki.

Recovery actions are registered by trusted application code. This module never
runs shell commands, imports plugins dynamically, or invents actions from model
output. A recovery callback should target one component and be safe to repeat.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class RecoveryStatus(str, Enum):
    RECOVERED = "recovered"
    FAILED = "failed"
    SKIPPED_COOLDOWN = "skipped_cooldown"
    UNKNOWN_COMPONENT = "unknown_component"


@dataclass(frozen=True)
class RecoveryResult:
    component: str
    status: RecoveryStatus
    duration_ms: float
    detail: str


RecoveryAction = Callable[[], Awaitable[bool]]


class SelfHealingManager:
    """Run explicit recovery callbacks with timeout, cooldown, and serialization."""
    def __init__(
        self,
        *,
        cooldown_seconds: float = 30.0,
        action_timeout_seconds: float = 10.0,
    ) -> None:
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        if action_timeout_seconds <= 0:
            raise ValueError("action_timeout_seconds must be > 0")
        self.cooldown_seconds = cooldown_seconds
        self.action_timeout_seconds = action_timeout_seconds
        self._actions: dict[str, RecoveryAction] = {}
        self._last_attempt: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def register(self, component: str, action: RecoveryAction) -> None:
        if not isinstance(component, str) or not component or component.strip() != component:
            raise ValueError("component must be a non-empty trimmed name")
        if not callable(action):
            raise TypeError("action must be callable")
        if component in self._actions:
            raise ValueError(f"recovery action already registered: {component}")
        self._actions[component] = action
        self._locks[component] = asyncio.Lock()

    async def recover(self, component: str) -> RecoveryResult:
        action = self._actions.get(component)
        if action is None:
            return RecoveryResult(
                component, RecoveryStatus.UNKNOWN_COMPONENT, 0.0,
                "no allowlisted recovery action",
            )
        async with self._locks[component]:
            now = time.monotonic()
            last = self._last_attempt.get(component)
            if last is not None and now - last < self.cooldown_seconds:
                return RecoveryResult(
                    component, RecoveryStatus.SKIPPED_COOLDOWN, 0.0,
                    "cooldown active",
                )
            self._last_attempt[component] = now
            started = time.monotonic()
            try:
                succeeded = await asyncio.wait_for(
                    action(), timeout=self.action_timeout_seconds
                )
                status = RecoveryStatus.RECOVERED if succeeded else RecoveryStatus.FAILED
                detail = (
                    "recovery action reported success" if succeeded
                    else "recovery action reported failure"
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                status, detail = RecoveryStatus.FAILED, "recovery action timed out"
            except Exception as exc:
                # Log type only; exception messages may contain secrets or PII.
                logger.warning(
                    "Recovery action failed for component %s (%s)",
                    component, type(exc).__name__,
                )
                status, detail = RecoveryStatus.FAILED, type(exc).__name__
            return RecoveryResult(
                component=component,
                status=status,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                detail=detail,
            )
