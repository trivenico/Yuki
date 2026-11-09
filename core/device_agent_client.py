"""Minimal cross-platform Yuki device heartbeat client.

The client reports bounded hardware metadata only. It does not execute jobs,
accept remote commands, or download models. Credentials are supplied out of
band and must be delivered over HTTPS except for explicit loopback development.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


MAX_RESPONSE_BYTES = 16_384


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= 2**60 else None


def _memory_bytes() -> tuple[int | None, int | None]:
    if os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            state = MEMORYSTATUSEX()
            state.dwLength = ctypes.sizeof(state)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
                return _positive_int(state.ullTotalPhys), _positive_int(state.ullAvailPhys)
        except (AttributeError, OSError, TypeError):
            pass
    else:
        try:
            page = os.sysconf("SC_PAGE_SIZE")
            total = page * os.sysconf("SC_PHYS_PAGES")
            available = page * os.sysconf("SC_AVPHYS_PAGES")
            return _positive_int(total), _positive_int(available)
        except (AttributeError, OSError, ValueError):
            pass
    return None, None


def discover_capabilities() -> dict[str, Any]:
    """Return conservative, privacy-minimal host capabilities."""
    total, available = _memory_bytes()
    result: dict[str, Any] = {
        "os": platform.system()[:80],
        "architecture": platform.machine()[:80],
        "cpu_cores": os.cpu_count() or 1,
    }
    if total is not None:
        result["ram_total_bytes"] = total
    if available is not None and (total is None or available <= total):
        result["ram_available_bytes"] = available
    try:
        free = shutil.disk_usage(Path.home()).free
        if _positive_int(free) is not None:
            result["storage_free_bytes"] = free
    except OSError:
        pass
    # GPU enumeration is deliberately optional and does not invoke shell tools.
    try:
        import pynvml  # type: ignore[import-not-found]
        pynvml.nvmlInit()
        gpus = []
        try:
            for index in range(min(pynvml.nvmlDeviceGetCount(), 8)):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                raw_name = pynvml.nvmlDeviceGetName(handle)
                name = raw_name.decode("utf-8", "replace") if isinstance(raw_name, bytes) else str(raw_name)
                gpus.append({
                    "name": name[:80],
                    "vendor": "NVIDIA",
                    "memory_total_bytes": int(memory.total),
                    "memory_available_bytes": int(memory.free),
                })
        finally:
            pynvml.nvmlShutdown()
        if gpus:
            result["gpus"] = gpus
    except (ImportError, Exception):
        # Hardware discovery must never prevent CPU-only agent operation.
        pass
    return result


class DeviceAgentClient:
    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        sequence_path: str | Path,
        timeout_seconds: float = 10.0,
        opener=None,
    ):
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("device endpoint must use HTTPS")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("invalid device endpoint")
        if not isinstance(token, str) or not 20 <= len(token) <= 256:
            raise ValueError("invalid device credential")
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("timeout must be between 1 and 60 seconds")
        self.endpoint = endpoint.rstrip("/") + "/v1/devices/heartbeat"
        self.token = token
        self.sequence_path = Path(sequence_path).expanduser()
        self.timeout_seconds = timeout_seconds
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context())
        )

    def _next_sequence(self) -> int:
        self.sequence_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            current = int(self.sequence_path.read_text(encoding="ascii").strip())
        except FileNotFoundError:
            current = 0
        except (ValueError, OSError) as exc:
            raise RuntimeError("device sequence state is unreadable; refusing unsafe reset") from exc
        if not 0 <= current < 2**63 - 1:
            raise RuntimeError("device sequence exhausted or invalid")
        next_value = current + 1
        temporary = self.sequence_path.with_name(self.sequence_path.name + ".tmp")
        temporary.write_text(str(next_value), encoding="ascii")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.sequence_path)
        return next_value

    def send_heartbeat(self, capabilities: dict[str, Any] | None = None, *, status: str = "idle") -> dict[str, Any]:
        if status not in {"idle", "busy", "degraded"}:
            raise ValueError("invalid status")
        payload = {
            "sequence": self._next_sequence(),
            "status": status,
            "capabilities": capabilities if capabilities is not None else discover_capabilities(),
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(body) > 16_000:
            raise ValueError("heartbeat payload too large")
        request = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            response = self.opener.open(request, timeout=self.timeout_seconds)
            try:
                data = response.read(MAX_RESPONSE_BYTES + 1)
                if len(data) > MAX_RESPONSE_BYTES:
                    raise RuntimeError("device API response too large")
                result = json.loads(data.decode("utf-8"))
            finally:
                response.close()
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 401, 403, 404, 413, 429}:
                raise RuntimeError(f"device API rejected heartbeat ({exc.code})") from None
            raise RuntimeError(f"device API unavailable ({exc.code})") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("device API connection failed") from exc
        if not isinstance(result, dict) or result.get("sequence") != payload["sequence"]:
            raise RuntimeError("device API returned an invalid heartbeat acknowledgement")
        return result

    def run(self, *, interval_seconds: int = 30, once: bool = False) -> None:
        if not 15 <= interval_seconds <= 300:
            raise ValueError("heartbeat interval must be between 15 and 300 seconds")
        while True:
            self.send_heartbeat()
            if once:
                return
            time.sleep(interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Yuki paired-device heartbeat client")
    parser.add_argument("--endpoint", default=os.environ.get("YUKI_DEVICE_ENDPOINT"))
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--sequence-file", default=os.environ.get(
        "YUKI_DEVICE_SEQUENCE_FILE", str(Path.home() / ".yuki" / "device-sequence")
    ))
    args = parser.parse_args(argv)
    token = os.environ.get("YUKI_DEVICE_TOKEN")
    if not args.endpoint or not token:
        parser.error("set YUKI_DEVICE_ENDPOINT and YUKI_DEVICE_TOKEN")
    try:
        client = DeviceAgentClient(args.endpoint, token, sequence_path=args.sequence_file)
        client.run(interval_seconds=args.interval, once=args.once)
    except (ValueError, RuntimeError) as exc:
        print(f"yuki-device-agent: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
