"""Allow-listed, cooldown-limited recovery actions. No shell or arbitrary repair."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable

class RecoveryStatus(str, Enum):
    RECOVERED = "recovered"
    FAILED = "failed"
    COOLDOWN = "cooldown"
    UNKNOWN_COMPONENT = "unknown_component"
    BUSY = "busy"

@dataclass(frozen=True)
class RecoveryResult:
    component: str
    status: RecoveryStatus
    message: str

Recovery = Callable[[], Awaitable[bool]]

class SelfHealingManager:
    """Run only explicitly registered recovery callbacks under per-component locks."""
    def __init__(self, recoveries: dict[str, Recovery], *, cooldown_seconds: float = 60,
                 action_timeout_seconds: float = 10, clock: Callable[[], float] = time.monotonic):
        if cooldown_seconds < 0 or action_timeout_seconds <= 0:
            raise ValueError("cooldown must be non-negative and timeout must be positive")
        if not isinstance(recoveries, dict):
            raise TypeError("recoveries must be a dictionary of named callbacks")
        for name, callback in recoveries.items():
            if not isinstance(name, str) or not name.strip() or not callable(callback):
                raise ValueError("recovery map must contain named callable actions")
        self._recoveries = dict(recoveries)
        self.cooldown_seconds = float(cooldown_seconds)
        self.action_timeout_seconds = float(action_timeout_seconds)
        self.clock = clock
        self._last_run: dict[str, float] = {}
        self._locks = {name: asyncio.Lock() for name in recoveries}

    async def recover(self, component: str) -> RecoveryResult:
        callback = self._recoveries.get(component)
        if callback is None:
            return RecoveryResult(component, RecoveryStatus.UNKNOWN_COMPONENT, "No recovery action is allow-listed.")
        lock = self._locks[component]
        if lock.locked():
            return RecoveryResult(component, RecoveryStatus.BUSY, "Recovery already running.")
        async with lock:
            now = self.clock()
            last = self._last_run.get(component)
            if last is not None and now - last < self.cooldown_seconds:
                return RecoveryResult(component, RecoveryStatus.COOLDOWN, "Recovery cooldown is active.")
            self._last_run[component] = now
            try:
                ok = await asyncio.wait_for(callback(), timeout=self.action_timeout_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                return RecoveryResult(component, RecoveryStatus.FAILED, "Recovery action failed; details withheld from user-facing output.")
            if ok is True:
                return RecoveryResult(component, RecoveryStatus.RECOVERED, "Recovery action reported success.")
            return RecoveryResult(component, RecoveryStatus.FAILED, "Recovery action did not report success.")
