"""Read-only host and AI usage telemetry for Yuki CLI and supervision UI.

Optional providers are detected lazily. Missing metrics are reported as unavailable;
this module never installs packages, starts services, or changes host configuration.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Any

from core.context_budget import context_budget


def _memory_from_proc() -> dict[str, Any] | None:
    """Read Linux memory counters without dependencies."""
    try:
        values = {}
        with open("/proc/meminfo", encoding="utf-8") as stream:
            for line in stream:
                key, _, rest = line.partition(":")
                if key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(rest.strip().split()[0]) * 1024
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if not total or available is None:
            return None
        used = max(0, total - available)
        return {"total_bytes": total, "used_bytes": used, "available_bytes": available,
                "percent": round(used * 100 / total, 1), "source": "procfs"}
    except (OSError, ValueError, IndexError):
        return None


def _gpu_metrics() -> dict[str, Any]:
    """Query NVIDIA telemetry if nvidia-smi is installed; never assume GPU availability."""
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"available": False, "detail": "NVIDIA telemetry unavailable (nvidia-smi not found)."}
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=1.5, check=False,
        )
        if result.returncode != 0:
            return {"available": False, "detail": "nvidia-smi could not read GPU telemetry."}
        devices = []
        for line in result.stdout.splitlines()[:8]:
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 4:
                continue
            try:
                utilization = int(parts[1])
                memory_used = int(parts[2])
                memory_total = int(parts[3])
            except ValueError:
                # nvidia-smi emits values such as [N/A] when a metric is unavailable.
                continue
            # Driver sentinel values must never be presented as real measurements.
            if not (0 <= utilization <= 100 and memory_used >= 0 and memory_total > 0
                    and memory_used <= memory_total):
                continue
            devices.append({
                "name": parts[0][:120] or "Unknown NVIDIA GPU",
                "utilization_percent": utilization,
                "memory_used_mib": memory_used,
                "memory_total_mib": memory_total,
            })
        if not devices:
            return {"available": False, "detail": "No valid NVIDIA telemetry was returned."}
        return {"available": True, "devices": devices, "detail": "NVIDIA GPU telemetry"}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "detail": "GPU telemetry probe failed or timed out."}


def collect_performance(*, token_summary: dict[str, Any] | None = None,
                        context_limit: int | None = None,
                        context_used: int | None = None) -> dict[str, Any]:
    """Collect best-effort local metrics; all unknown values remain explicitly unknown."""
    cpu: dict[str, Any] = {"percent": None, "logical_cores": os.cpu_count(),
                           "detail": "Install psutil for CPU utilization sampling."}
    memory = _memory_from_proc()
    try:
        import psutil  # optional dependency
        cpu["percent"] = psutil.cpu_percent(interval=0.0)
        cpu["detail"] = "psutil"
        virtual = psutil.virtual_memory()
        memory = {"total_bytes": int(virtual.total), "used_bytes": int(virtual.used),
                  "available_bytes": int(virtual.available), "percent": float(virtual.percent),
                  "source": "psutil"}
    except (ImportError, OSError, RuntimeError):
        pass

    context = context_budget(limit_tokens=context_limit, used_tokens=context_used)

    safe_token_summary = token_summary if isinstance(token_summary, dict) else {
        "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "exact_events": 0, "estimated_events": 0, "alerts": [],
        "detail": "No valid token usage summary is available."
    }
    return {
        "platform": platform.system(),
        "cpu": cpu,
        "memory": memory or {"total_bytes": None, "used_bytes": None,
                             "available_bytes": None, "percent": None, "source": "unavailable"},
        "gpu": _gpu_metrics(),
        "tokens": safe_token_summary,
        "context": context,
    }


def format_bytes(value: int | None) -> str:
    if type(value) is not int or value < 0:
        return "Unknown"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return "Unknown"


def format_performance_report(metrics: dict[str, Any]) -> str:
    cpu = metrics["cpu"]
    memory = metrics["memory"]
    gpu = metrics["gpu"]
    tokens = metrics["tokens"]
    context = metrics["context"]
    cpu_usage = f"{cpu['percent']:.1f}%" if isinstance(cpu.get("percent"), (int, float)) else "Unknown"
    lines = [
        "Yuki performance (read-only host telemetry)",
        f"Platform: {metrics['platform']}",
        f"CPU: {cpu_usage} | logical cores: {cpu.get('logical_cores') or 'Unknown'}",
        f"RAM: {format_bytes(memory.get('used_bytes'))} used / {format_bytes(memory.get('total_bytes'))} total"
        + (f" ({memory['percent']:.1f}%)" if isinstance(memory.get("percent"), (int, float)) else ""),
    ]
    if gpu.get("available"):
        for index, device in enumerate(gpu["devices"], 1):
            lines.append(
                f"GPU {index}: {device['name']} | {device['utilization_percent']}% | "
                f"VRAM {device['memory_used_mib']} / {device['memory_total_mib']} MiB"
            )
    else:
        lines.append(f"GPU: {gpu.get('detail', 'Unknown')}")
    lines.extend([
        f"Tokens recorded: {tokens.get('total_tokens', 0)} total "
        f"({tokens.get('input_tokens', 0)} input / {tokens.get('output_tokens', 0)} output)",
        f"Token events: {tokens.get('exact_events', 0)} exact, "
        f"{tokens.get('estimated_events', 0)} estimated",
        f"Remaining model context: {context.get('remaining_tokens') if context.get('remaining_tokens') is not None else 'Unknown'}",
        f"Context: {context.get('detail', 'Unknown')}",
        "Note: token totals include only events recorded by Yuki; they are not a provider-wide quota.",
    ])
    if tokens.get("alerts"):
        lines.append("Token alerts: " + ", ".join(str(alert) for alert in tokens["alerts"]))
    return "\n".join(lines)
