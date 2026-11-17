"""Explicitly controlled, read-only security dashboard panel.

This widget is deliberately passive: it creates no sensors, timers, workers,
network clients, or persistence. The owning window supplies callbacks and
marshals their results back to the GUI thread.
"""
from __future__ import annotations

import re
from typing import Callable


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _plain(value, limit: int = 160) -> str:
    if not isinstance(value, str):
        return "Unknown"
    return _CONTROL_CHARS.sub("", value[:limit]).strip() or "Unknown"


class SecurityDashboardPanel:
    """Factory namespace for a lazily imported Qt panel.

    Constructing the panel never starts monitoring. The Start button asks for
    confirmation before invoking the supplied start callback. Sensor work must
    be performed by the owner on a background worker, not in these callbacks.
    """

    @staticmethod
    def create(parent=None, *, on_start: Callable[[], None],
               on_stop: Callable[[], None], on_refresh: Callable[[], None]):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton,
            QVBoxLayout, QWidget,
        )

        for callback in (on_start, on_stop, on_refresh):
            if not callable(callback):
                raise TypeError("Dashboard callbacks must be callable.")

        page = QWidget(parent)
        page.setObjectName("yukiSecurityDashboard")
        layout = QVBoxLayout(page)
        heading = QLabel("Desktop security")
        heading.setObjectName("securityHeading")
        layout.addWidget(heading)

        explanation = QLabel(
            "Read-only monitoring. No process termination, firewall changes, "
            "or automatic remediation. Monitoring stays off until you start it."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        status = QLabel("Monitoring is off.")
        status.setObjectName("securityStatus")
        status.setTextFormat(Qt.PlainText)
        layout.addWidget(status)

        actions = QHBoxLayout()
        start = QPushButton("Start monitoring")
        stop = QPushButton("Stop monitoring")
        refresh = QPushButton("Refresh")
        stop.setEnabled(False)
        refresh.setEnabled(False)
        actions.addWidget(start)
        actions.addWidget(stop)
        actions.addWidget(refresh)
        layout.addLayout(actions)

        process_heading = QLabel("Process inventory")
        layout.addWidget(process_heading)
        process_summary = QLabel("No snapshot collected.")
        process_summary.setTextFormat(Qt.PlainText)
        layout.addWidget(process_summary)
        processes = QListWidget()
        processes.setObjectName("securityProcesses")
        layout.addWidget(processes, 1)

        alert_heading = QLabel("Security alerts")
        layout.addWidget(alert_heading)
        alerts = QListWidget()
        alerts.setObjectName("securityAlerts")
        layout.addWidget(alerts, 1)

        class Presenter:
            def set_state(self, state: str, *, enabled: bool):
                safe_state = _plain(state, 80)
                status.setText("Monitoring: " + safe_state)
                start.setEnabled(not enabled)
                stop.setEnabled(enabled)
                refresh.setEnabled(enabled)
                if not enabled:
                    # Do not leave stale process names or alerts visible after
                    # monitoring stops, is revoked, or the session expires.
                    processes.clear()
                    alerts.clear()
                    process_summary.setText("No snapshot collected.")

            def render(self, snapshot: dict):
                if (
                    not isinstance(snapshot, dict)
                    or snapshot.get("read_only") is not True
                    or snapshot.get("persisted") is not False
                    or snapshot.get("automated_response") is not False
                    or snapshot.get("enabled") is not True
                ):
                    raise ValueError("An enabled, read-only, non-persistent snapshot is required.")
                raw_processes = snapshot.get("processes", [])
                raw_alerts = snapshot.get("alerts", [])
                if not isinstance(raw_processes, list) or not isinstance(raw_alerts, list):
                    raise ValueError("Security snapshot lists are invalid.")
                # Match the upstream monitor contract and reject malformed or
                # unexpectedly large snapshots before touching any Qt widgets.
                if len(raw_processes) > 5000 or len(raw_alerts) > 1100:
                    raise ValueError("Security snapshot exceeds the dashboard limits.")
                processes.clear()
                for item in raw_processes[:500]:
                    if not isinstance(item, dict):
                        continue
                    pid = item.get("pid")
                    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                        continue
                    # Plain-text item rendering prevents process names becoming markup.
                    processes.addItem(f"{pid}  {_plain(item.get('name'))}  ({_plain(item.get('status'), 80)})")
                count = snapshot.get("process_count", len(raw_processes))
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    count = len(raw_processes)
                truncated = snapshot.get("process_truncated") is True
                summary = (
                    f"{min(count, 10_000_000)} process records collected; "
                    f"{len(processes)} displayed."
                )
                if truncated:
                    summary += " Inventory capped; additional processes were not inspected."
                process_summary.setText(summary)
                alerts.clear()
                for alert in raw_alerts[:100]:
                    if not isinstance(alert, dict):
                        continue
                    kind = _plain(alert.get("type"), 80)
                    # Never render arbitrary detector-provided message text.
                    message = {
                        "possible_port_scan": "Possible port scan detected; verify network activity.",
                        "configured_process_name_match": "A process matched your configured watchlist; verify its origin.",
                    }.get(kind, "A security event was detected; review activity.")
                    alerts.addItem(message)

        presenter = Presenter()

        def confirm_start():
            answer = QMessageBox.question(
                page,
                "Start read-only monitoring",
                "Yuki will inspect a bounded process inventory and authorized "
                "connection metadata for security alerts. It will not terminate "
                "processes, change firewall rules, or upload monitoring data. "
                "Start monitoring for this session?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                on_start()

        start.clicked.connect(confirm_start)
        stop.clicked.connect(on_stop)
        refresh.clicked.connect(on_refresh)
        page.security_presenter = presenter
        page.security_controls = {
            "status": status,
            "start": start,
            "stop": stop,
            "refresh": refresh,
            "process_summary": process_summary,
            "processes": processes,
            "alerts": alerts,
        }
        return page
