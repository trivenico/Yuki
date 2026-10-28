"""Opt-in background scheduler for local, proposal-only evolution reviews."""
from __future__ import annotations

from datetime import datetime
from threading import Event, Thread
from typing import Callable

from companion.evolution import DailyEvolutionCycle


class EvolutionScheduler:
    """Run one local review cycle per local day after a configured hour.

    The cycle reads explicitly recorded observations and creates review proposals.
    It never applies code/config changes or executes actions. The scheduler is
    opt-in, daemonized, and stoppable; errors are reported through a callback.
    """

    def __init__(
        self,
        cycle: DailyEvolutionCycle,
        *,
        enabled: bool = False,
        hour: int = 3,
        poll_seconds: int = 60,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
            raise ValueError("hour must be an integer from 0 to 23.")
        if not isinstance(poll_seconds, int) or isinstance(poll_seconds, bool) or poll_seconds < 1:
            raise ValueError("poll_seconds must be a positive integer.")
        self.cycle = cycle
        self.enabled = enabled
        self.hour = hour
        self.poll_seconds = poll_seconds
        self.on_error = on_error
        self._stop = Event()
        self._thread: Thread | None = None

    def run_if_due(self, now: datetime | None = None) -> bool:
        """Run the review if enabled, past the local hour, and not yet run today."""
        if not self.enabled:
            return False
        current = (now or datetime.now().astimezone()).astimezone()
        if current.hour < self.hour:
            return False
        status = self.cycle.status()
        last = status.get("last_report_at")
        if last:
            try:
                if datetime.fromisoformat(last).astimezone().date() == current.date():
                    return False
            except (TypeError, ValueError):
                # Malformed state is not permission to overwrite it automatically.
                raise RuntimeError("Evolution report timestamp is invalid; background run skipped.")
        self.cycle.run_cycle(now=current)
        return True

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="yuki-evolution-review", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.run_if_due()
            except Exception as exc:
                if self.on_error:
                    self.on_error(exc)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.0, timeout))
