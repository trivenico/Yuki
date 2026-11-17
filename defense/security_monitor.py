"""Unified, opt-in, read-only desktop security monitor.

The facade coordinates injected sensor primitives only. It never starts
background collection, captures packets, persists telemetry, or responds.
"""
from __future__ import annotations

from collections import deque
from html import escape
import re
from threading import RLock

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _safe_label(value, limit=300):
    if not isinstance(value, str):
        return "unknown"
    cleaned = _CONTROL_CHARS.sub("", value[:limit]).strip()
    return escape(cleaned, quote=True) if cleaned else "unknown"


class SecurityMonitorError(RuntimeError):
    """The security monitor is disabled or a sensor failed safely."""


class DesktopSecurityMonitor:
    """Combine process inventory and connection alerts behind one safe interface."""

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
        self._generation = 0
        self._alerts = deque(maxlen=max_alerts)
        self._lock = RLock()

    @property
    def enabled(self):
        with self._lock:
            return self._enabled

    def enable(self):
        """Enable explicit calls; this does not start capture or background work."""
        with self._lock:
            if not self._enabled:
                self._generation += 1
            self._enabled = True

    def disable(self):
        """Invalidate in-flight calls and clear in-memory alert history immediately."""
        with self._lock:
            self._enabled = False
            self._generation += 1
            self._alerts.clear()

    def _active_generation(self):
        with self._lock:
            if not self._enabled:
                raise SecurityMonitorError("Desktop security monitoring is disabled.")
            return self._generation

    def observe_connection(self, source_ip, destination_port, *, timestamp=None):
        """Feed caller-authorized connection metadata to the alert-only detector."""
        generation = self._active_generation()
        detector = self._port_scan_detector
        if detector is None:
            raise SecurityMonitorError("No connection detector is configured.")
        try:
            alert = detector.observe(source_ip, destination_port, timestamp=timestamp)
        except (TypeError, ValueError) as exc:
            raise SecurityMonitorError("Connection metadata was rejected.") from exc
        except Exception as exc:
            raise SecurityMonitorError("Connection detector failed safely.") from exc
        if alert is not None and not isinstance(alert, dict):
            raise SecurityMonitorError("Connection detector returned an invalid alert.")
        summary = None
        with self._lock:
            if not self._enabled or self._generation != generation:
                raise SecurityMonitorError("Monitoring was disabled during collection.")
            if alert is not None:
                summary = {
                    "source": "network",
                    "type": _safe_label(alert.get("type", "security_alert"), 80),
                    "message": (
                        "Possible port scan detected; verify network activity."
                        if _safe_label(alert.get("type", ""), 80) == "possible_port_scan"
                        else "A network security event was detected; review activity."
                    ),
                }
                self._alerts.append(summary)
        return summary

    def snapshot(self):
        """Return a bounded, in-memory dashboard view; disable never waits on a sensor."""
        generation = self._active_generation()
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
                        "type": _safe_label(alert.get("type", "security_alert"), 80),
                        "message": (
                            "A process matched your configured watchlist; verify its origin."
                            if _safe_label(alert.get("type", ""), 80) == "configured_process_name_match"
                            else "A process security event was detected; review activity."
                        ),
                    })

        processes = []
        process_count = 0
        process_truncated = False
        if process_data is not None:
            raw_processes = process_data.get("processes", [])
            if not isinstance(raw_processes, list):
                raise SecurityMonitorError("Process inventory returned invalid process records.")
            for item in raw_processes[:5000]:
                if not isinstance(item, dict):
                    continue
                pid = item.get("pid")
                if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                    continue
                processes.append({
                    "pid": pid,
                    "name": _safe_label(item.get("name"), 160),
                    "status": _safe_label(item.get("status"), 80),
                })
            supplied_count = process_data.get("process_count", len(processes))
            if (isinstance(supplied_count, int) and not isinstance(supplied_count, bool)
                    and len(processes) <= supplied_count <= 10_000_000):
                process_count = supplied_count
            else:
                process_count = len(processes)
            process_truncated = process_data.get("truncated") is True

        with self._lock:
            if not self._enabled or self._generation != generation:
                raise SecurityMonitorError("Monitoring was disabled during collection.")
            alerts = process_alerts + list(self._alerts)
        return {
            "enabled": True,
            "processes": processes,
            "process_count": process_count,
            "process_truncated": process_truncated,
            "alerts": alerts,
            "read_only": True,
            "persisted": False,
            "automated_response": False,
        }
