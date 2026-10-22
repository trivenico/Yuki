"""Bounded, thread-safe in-memory history for numeric performance telemetry.

Only selected numeric measurements and timestamps are retained. Prompts, model output,
credentials, filesystem paths, and raw provider payloads are deliberately excluded.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import RLock
from typing import Any


class PerformanceHistory:
    """Keep a fixed-size ring buffer of safe numeric host/runtime measurements."""

    def __init__(self, max_samples: int = 300) -> None:
        if type(max_samples) is not int or not 1 <= max_samples <= 10000:
            raise ValueError("max_samples must be an integer between 1 and 10000")
        self.max_samples = max_samples
        self._samples: deque[dict[str, Any]] = deque(maxlen=max_samples)
        self._lock = RLock()

    @staticmethod
    def _number(value: Any, *, minimum: float = 0, maximum: float | None = None) -> int | float | None:
        if type(value) not in (int, float):
            return None
        if value < minimum or (maximum is not None and value > maximum):
            return None
        return value

    def record(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Store a sanitized snapshot and return a detached copy."""
        if not isinstance(metrics, dict):
            raise TypeError("metrics must be a dictionary")
        cpu = metrics.get("cpu") if isinstance(metrics.get("cpu"), dict) else {}
        memory = metrics.get("memory") if isinstance(metrics.get("memory"), dict) else {}
        tokens = metrics.get("tokens") if isinstance(metrics.get("tokens"), dict) else {}
        gpu = metrics.get("gpu") if isinstance(metrics.get("gpu"), dict) else {}
        devices = []
        for device in gpu.get("devices", [])[:8] if isinstance(gpu.get("devices"), list) else []:
            if not isinstance(device, dict):
                continue
            devices.append({
                "utilization_percent": self._number(device.get("utilization_percent"), maximum=100),
                "memory_used_mib": self._number(device.get("memory_used_mib")),
                "memory_total_mib": self._number(device.get("memory_total_mib")),
            })
        sample = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "platform": str(metrics.get("platform", "Unknown"))[:32],
            "cpu_percent": self._number(cpu.get("percent"), maximum=100),
            "ram_percent": self._number(memory.get("percent"), maximum=100),
            "ram_used_bytes": self._number(memory.get("used_bytes")),
            "ram_total_bytes": self._number(memory.get("total_bytes")),
            "gpu": devices,
            "total_tokens": self._number(tokens.get("total_tokens")),
            "input_tokens": self._number(tokens.get("input_tokens")),
            "output_tokens": self._number(tokens.get("output_tokens")),
            "remaining_context_tokens": self._number(
                (metrics.get("context") or {}).get("remaining_tokens")
                if isinstance(metrics.get("context"), dict) else None
            ),
        }
        with self._lock:
            self._samples.append(sample)
            return dict(sample, gpu=[dict(item) for item in sample["gpu"]])

    def snapshot(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return detached samples in chronological order; never expose internal state."""
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit must be a non-negative integer or None")
        with self._lock:
            samples = list(self._samples)
        if limit is not None:
            samples = samples[-limit:] if limit else []
        return [dict(item, gpu=[dict(device) for device in item["gpu"]]) for item in samples]

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._samples)


PERFORMANCE_HISTORY = PerformanceHistory()


def format_performance_history(history: PerformanceHistory = PERFORMANCE_HISTORY,
                              limit: int = 10) -> str:
    """Render a compact trend view without implying unavailable readings are zero."""
    samples = history.snapshot(limit=limit)
    if not samples:
        return "No performance history yet. Run /performance or leave the supervision dashboard open."
    lines = [f"Yuki performance history (latest {len(samples)} of max {history.max_samples} samples)"]
    for sample in samples:
        timestamp = sample["timestamp"].replace("+00:00", "Z")
        cpu = f"{sample['cpu_percent']:.1f}%" if sample["cpu_percent"] is not None else "Unknown"
        ram = f"{sample['ram_percent']:.1f}%" if sample["ram_percent"] is not None else "Unknown"
        tokens = sample["total_tokens"] if sample["total_tokens"] is not None else "Unknown"
        context = (sample["remaining_context_tokens"]
                   if sample["remaining_context_tokens"] is not None else "Unknown")
        gpu = ", ".join(
            f"{device['utilization_percent']}%"
            if device["utilization_percent"] is not None else "Unknown"
            for device in sample["gpu"]
        ) or "Unavailable"
        lines.append(f"{timestamp} | CPU {cpu} | RAM {ram} | GPU {gpu} | recorded tokens {tokens} | latest-request context remaining {context}")
    return "\n".join(lines)
