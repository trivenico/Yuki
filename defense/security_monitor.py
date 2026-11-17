"""Unified, opt-in, read-only desktop security monitor.

This facade coordinates injected sensor primitives. It does not start background
threads, capture packets, persist telemetry, or perform response actions.
"""
from __future__ import annotations

from collections import deque
from threading import RLock


class SecurityMonitorError(RuntimeError):
    """The security monitor is disabled or a sensor failed safely."""


class DesktopSecurityMonitor:
    """Combine process inventory and connection alerts behind one safe interface.

    Sensor objects are injected so optional OS dependencies remain lazy and each
    sensor can be independently permission-gated by the desktop application.
    """

    def __init__(self, *, process_monitor=None, port_scan_detector=None,
                 enabled=False, max_alerts=100):
        if enabled is not False and enabled is not True:
            raise ValueError("enabled must be a boolean")
        if (isinstance(max_alerts, bool) or not isinstance(max_alerts, int)
                or not 1 <= max_alerts <= 1000):
            raise ValueError("max_alerts must be an integer from 1 to 1000")
        if process_monitor is not None and not callable(getattr(process_monitor, "snapshot", None)):
            raise ValueError("process_monitor must provide snapshot()")
        if port_scan_detector is not None and not callable(getattr(port_scan_detector, "observe", None)):
            raise ValueError("port_scan_detector must provide observe()")
        self._process_monitor = process_monitor
        self._port_scan_detector = port_scan_detector
        self._enabled = enabled
        self._alerts = deque(maxlen=max_alerts)
        self._lock = RLock()

    @property
    def enabled(self):
        return self._enabled

    def enable(self):
        """Enable explicit calls; this does not start capture or background work."""
        with self._lock:
            self._enabled = True

    def disable(self):
        """Disable collection and discard in-memory alert history."""
        with self._lock:
            self._enabled = False
            self._alerts.clear()

    def observe_connection(self, source_ip, destination_port, *, timestamp=None):
        """Feed caller-authorized connection metadata to the alert-only detector."""
        with self._lock:
            if not self._enabled:
                raise SecurityMonitorError("Desktop security monitoring is disabled.")
            if self._port_scan_detector is None:
                raise SecurityMonitorError("No connection detector is configured.")
            try:
                alert = self._port_scan_detector.observe(
                    source_ip, destination_port, timestamp=timestamp
                )
            except TypeError as exc:
                # Do not silently retry with fewer arguments: that could change
                # the timestamp semantics of a detector implementation.
                raise SecurityMonitorError("Connection metadata was rejected.") from exc
            except ValueError as exc:
                raise SecurityMonitorError("Connection metadata was rejected.") from exc
            if alert is not None:
                self._alerts.append({
                    "source": "network",
                    "type": str(alert.get("type", "security_alert"))[:80],
                    "message": str(alert.get("message", "Security event detected."))[:300],
                })
            return alert

    def snapshot(self):
        """Return a bounded, in-memory dashboard view; no telemetry is persisted."""
        with self._lock:
            if not self._enabled:
                raise SecurityMonitorError("Desktop security monitoring is disabled.")
            process_data = None
            process_alerts = []
            if self._process_monitor is not None:
                try:
                    process_data = self._process_monitor.snapshot()
                except Exception as exc:
                    raise SecurityMonitorError("Process inventory failed safely.") from exc
                if not isinstance(process_data, dict):
                    raise SecurityMonitorError("Process inventory returned an invalid result.")
                raw_alerts = process_data.get("alerts", [])
                if not isinstance(raw_alerts, list):
                    raise SecurityMonitorError("Process inventory returned invalid alerts.")
                for alert in raw_alerts[:100]:
                    if isinstance(alert, dict):
                        process_alerts.append({
                            "source": "process",
                            "type": str(alert.get("type", "security_alert"))[:80],
                            "message": str(alert.get("message", "Process watchlist match."))[:300],
                        })
            return {
                "enabled": True,
                "processes": process_data.get("processes", []) if process_data else [],
                "process_count": process_data.get("process_count", 0) if process_data else 0,
                "process_truncated": process_data.get("truncated", False) if process_data else False,
                "alerts": process_alerts + list(self._alerts),
                "read_only": True,
                "persisted": False,
                "automated_response": False,
            }
