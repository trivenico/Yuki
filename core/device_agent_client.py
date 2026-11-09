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
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse


MAX_RESPONSE_BYTES = 16_384


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= 2**60 else None


def _linux_cgroup_paths(
    *, proc_cgroup: Path = Path("/proc/self/cgroup"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> dict[str, Path]:
    """Resolve process-relative cgroup v1/v2 directories without shell calls."""
    paths: dict[str, Path] = {}
    try:
        lines = proc_cgroup.read_text(encoding="ascii").splitlines()[:32]
    except OSError:
        return paths
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        controllers, relative = parts[1], parts[2].lstrip("/")
        if ".." in Path(relative).parts:
            continue
        if not controllers:
            paths["v2"] = cgroup_root / relative
        else:
            names = set(controllers.split(","))
            for name in ("memory", "cpu", "cpuacct"):
                if name in names:
                    paths[name] = Path("/sys/fs/cgroup") / name / relative
    # Common unified hierarchy mount and v1 controller mounts.
    if not paths.get("v2") and (cgroup_root / "memory.max").exists():
        paths["v2"] = cgroup_root
    return paths


def _mountinfo_cgroup_mounts(mountinfo: Path) -> list[tuple[str, str, Path, Path]]:
    """Return cgroup mounts as (filesystem, controllers, hierarchy_root, mountpoint)."""
    mounts: list[tuple[str, str, Path, Path]] = []
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()[:256]
    except OSError:
        return mounts
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
            if separator < 6 or len(fields) <= separator + 3:
                continue
            filesystem = fields[separator + 1]
            if filesystem not in {"cgroup", "cgroup2"}:
                continue
            root = PurePosixPath(fields[3].replace("\\\\040", " ").replace("\\\\011", "\\t").replace("\\\\134", "\\\\"))
            mountpoint = Path(fields[4].replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\"))
            controllers = set(fields[separator + 3].split(","))
            controllers.update(fields[separator + 2].split(","))
            mounts.append((filesystem, ",".join(sorted(controllers)), root, mountpoint))
        except (ValueError, IndexError):
            continue
    return mounts


def _resolve_cgroup_mount(membership: str, hierarchy_root: Path, mountpoint: Path) -> Path | None:
    """Map a hierarchy membership into its mount without escaping the mount."""
    try:
        member = PurePosixPath(membership)
        relative_root = hierarchy_root
        if not member.is_absolute():
            return None
        relative = member.relative_to(relative_root)
        base = mountpoint.resolve()
        candidate = (base.joinpath(*relative.parts)).resolve()
        candidate.relative_to(base)
        return candidate
    except (ValueError, OSError, RuntimeError):
        return None


def _linux_cgroup_paths(
    *, proc_cgroup: Path = Path("/proc/self/cgroup"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    mountinfo: Path = Path("/proc/self/mountinfo"),
) -> dict[str, Path]:
    """Resolve process cgroup directories from membership and actual mount metadata."""
    paths: dict[str, Path] = {}
    try:
        lines = proc_cgroup.read_text(encoding="ascii").splitlines()[:32]
    except OSError:
        return paths
    memberships: dict[str, str] = {}
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        controllers, relative = parts[1], parts[2]
        if not relative.startswith("/") or ".." in Path(relative).parts:
            continue
        if not controllers:
            memberships["v2"] = relative
        else:
            for name in ("memory", "cpu", "cpuacct"):
                if name in controllers.split(","):
                    memberships[name] = relative

    mounts = _mountinfo_cgroup_mounts(mountinfo)
    for filesystem, controllers, hierarchy_root, mountpoint in mounts:
        if filesystem == "cgroup2" and "v2" in memberships:
            resolved = _resolve_cgroup_mount(memberships["v2"], hierarchy_root, mountpoint)
            if resolved is not None:
                paths["v2"] = resolved
        elif filesystem == "cgroup":
            available = set(controllers.split(","))
            for name in ("memory", "cpu", "cpuacct"):
                if name in memberships and name in available:
                    resolved = _resolve_cgroup_mount(memberships[name], hierarchy_root, mountpoint)
                    if resolved is not None:
                        paths[name] = resolved

    # Compatibility fallback for systems where mountinfo is unavailable.
    if not mounts:
        for name in ("memory", "cpu", "cpuacct"):
            if name in memberships:
                candidate = Path("/sys/fs/cgroup") / name / memberships[name].lstrip("/")
                try:
                    candidate.resolve().relative_to(Path("/sys/fs/cgroup").resolve())
                    paths[name] = candidate
                except (ValueError, OSError, RuntimeError):
                    pass
        if "v2" in memberships:
            candidate = cgroup_root / memberships["v2"].lstrip("/")
            try:
                candidate.resolve().relative_to(cgroup_root.resolve())
                paths["v2"] = candidate
            except (ValueError, OSError, RuntimeError):
                pass
    if not paths.get("v2") and (cgroup_root / "memory.max").exists():
        paths["v2"] = cgroup_root
    return paths


def _read_cgroup_int(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="ascii").strip()
        if raw == "max":
            return None
        value = int(raw)
        return value if 0 < value <= 2**60 else None
    except (OSError, ValueError):
        return None


def _effective_linux_memory(
    total: int | None, available: int | None, *, paths: dict[str, Path] | None = None,
) -> tuple[int | None, int | None]:
    """Clamp host memory to the process cgroup limit and remaining budget."""
    paths = paths if paths is not None else _linux_cgroup_paths()
    v2 = paths.get("v2")
    if v2:
        limit = _read_cgroup_int(v2 / "memory.max")
        usage = _read_cgroup_int(v2 / "memory.current")
    else:
        memory = paths.get("memory")
        limit = _read_cgroup_int(memory / "memory.limit_in_bytes") if memory else None
        usage = _read_cgroup_int(memory / "memory.usage_in_bytes") if memory else None
    if limit is not None:
        total = min(total, limit) if total is not None else limit
        # Unknown usage must not be treated as zero: fail closed for
        # schedulers rather than advertising the entire cgroup limit as free.
        remaining = max(0, limit - usage) if usage is not None else 0
        available = min(available, remaining) if available is not None else remaining
    if total is not None and available is not None:
        available = min(available, total)
    return total, available


def _clamp_cpu_quota(affinity: int, *, paths: dict[str, Path]) -> int:
    effective = max(1, affinity)
    v2 = paths.get("v2")
    quota_path = v2 / "cpu.max" if v2 else None
    if quota_path:
        try:
            quota, period = quota_path.read_text(encoding="ascii").split()[:2]
            if quota != "max":
                q, p = int(quota), int(period)
                if q > 0 and p > 0:
                    effective = min(effective, max(1, q // p))
        except (OSError, ValueError):
            pass
    else:
        cpu = paths.get("cpu")
        if cpu:
            quota = _read_cgroup_int(cpu / "cpu.cfs_quota_us")
            period = _read_cgroup_int(cpu / "cpu.cfs_period_us")
            if quota and period:
                effective = min(effective, max(1, quota // period))
    return effective


def _effective_cpu_millicores(*, affinity: int | None = None, paths: dict[str, Path] | None = None) -> int:
    """Return conservative CPU capacity in millicores (1000 = one CPU)."""
    if affinity is None:
        try:
            affinity = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            affinity = os.cpu_count() or 1
    affinity = max(1, affinity)
    if paths is None:
        paths = _linux_cgroup_paths() if sys.platform.startswith("linux") else {}
    quota = None
    period = None
    v2 = paths.get("v2")
    if v2:
        try:
            raw_quota, raw_period = (v2 / "cpu.max").read_text(encoding="ascii").split()[:2]
            if raw_quota != "max":
                quota, period = int(raw_quota), int(raw_period)
        except (OSError, ValueError):
            pass
    else:
        cpu = paths.get("cpu")
        if cpu:
            quota = _read_cgroup_int(cpu / "cpu.cfs_quota_us")
            period = _read_cgroup_int(cpu / "cpu.cfs_period_us")
    capacity = affinity * 1000
    if quota is not None and period is not None and quota > 0 and period > 0:
        capacity = min(capacity, (quota * 1000) // period)
    return max(0, capacity)


def _effective_cpu_cores() -> int:
    """Return backward-compatible integer CPU capacity."""
    return _effective_cpu_millicores() // 1000


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
            total, available = _positive_int(total), _positive_int(available)
            if sys.platform.startswith("linux"):
                return _effective_linux_memory(total, available)
            return total, available
        except (AttributeError, OSError, ValueError):
            pass
    return None, None


def discover_capabilities() -> dict[str, Any]:
    """Return conservative, privacy-minimal effective process capabilities."""
    total, available = _memory_bytes()
    cpu_cores = _effective_cpu_cores()
    cpu_millicores = _effective_cpu_millicores()
    result: dict[str, Any] = {
        "os": platform.system()[:80],
        "architecture": platform.machine()[:80],
        "cpu_cores": cpu_cores,
        "cpu_millicores": cpu_millicores,
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
