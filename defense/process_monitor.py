"""Privacy-conscious, read-only desktop process inventory.

Only process ID, executable display name, and process status are exposed.
Command lines, environment variables, usernames, and executable paths are
intentionally excluded. This module never terminates or modifies processes.
"""
from __future__ import annotations

from itertools import islice
import re


class ProcessMonitorError(RuntimeError):
    """The process inventory could not be collected safely."""


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean_name(value):
    if not isinstance(value, str):
        return "unknown"
    value = _CONTROL_CHARS.sub("", value[:512]).strip()
    return value[:160] or "unknown"


class ReadOnlyProcessMonitor:
    """Collect a bounded process snapshot and optional exact-name alerts."""

    def __init__(self, *, max_processes=2048, suspicious_names=(), provider=None):
        if isinstance(max_processes, bool) or not isinstance(max_processes, int) or not 1 <= max_processes <= 5000:
            raise ValueError("max_processes must be an integer from 1 to 5000")
        if isinstance(suspicious_names, (str, bytes)):
            raise ValueError("suspicious_names must be an iterable of process names")
        names = set()
        try:
            iterator = iter(suspicious_names)
            for index, name in enumerate(islice(iterator, 1001)):
                if index >= 1000:
                    raise ValueError("suspicious_names cannot contain more than 1000 entries")
                if not isinstance(name, str) or not name.strip() or len(name) > 160:
                    raise ValueError("Each suspicious process name must be 1-160 characters")
                names.add(_clean_name(name).casefold())
        except TypeError as exc:
            raise ValueError("suspicious_names must be iterable") from exc
        self.max_processes = max_processes
        self.suspicious_names = frozenset(names)
        self._provider = provider

    def _iter_processes(self):
        if self._provider is not None:
            return self._provider()
        try:
            import psutil
        except ImportError as exc:
            raise ProcessMonitorError(
                "Install the optional psutil dependency to inspect local processes."
            ) from exc
        return psutil.process_iter(attrs=["pid", "name", "status"], ad_value=None)

    def snapshot(self):
        """Return at most max_processes records; no state is persisted."""
        try:
            iterator = iter(self._iter_processes())
            raw_records = list(islice(iterator, self.max_processes + 1))
        except ProcessMonitorError:
            raise
        except Exception as exc:
            raise ProcessMonitorError("The process inventory could not be read.") from exc

        truncated = len(raw_records) > self.max_processes
        records = []
        seen = set()
        for raw in raw_records[:self.max_processes]:
            try:
                info = getattr(raw, "info", raw)
            except Exception:
                # A process may exit or become inaccessible between enumeration
                # and metadata access. Skip that record without leaking details.
                continue
            if not isinstance(info, dict):
                continue
            pid = info.get("pid")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            name = _clean_name(info.get("name"))
            status = _clean_name(info.get("status"))
            records.append({"pid": pid, "name": name, "status": status})

        alerts = []
        for item in records:
            if item["name"].casefold() in self.suspicious_names:
                alerts.append({
                    "type": "configured_process_name_match",
                    "pid": item["pid"],
                    "process_name": item["name"],
                    "message": "Process name matched a user-configured watchlist; verify its origin before taking action.",
                })

        return {
            "processes": records,
            "process_count": len(records),
            "truncated": truncated,
            "alerts": alerts,
            "read_only": True,
            "persisted": False,
        }
