#!/usr/bin/env python3
"""Read-only preflight for a Linux Yuki deployment (no installs or network calls)."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path


def _read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value.strip().strip('"').strip("'")
    except (OSError, UnicodeError):
        pass
    return values


def collect_report(*, target: Path = Path.home()) -> dict:
    release = _read_os_release()
    architecture = platform.machine().lower()
    is_linux = sys.platform.startswith("linux")
    is_arm64 = architecture in {"aarch64", "arm64"}
    ubuntu_24 = release.get("ID", "").lower() == "ubuntu" and release.get("VERSION_ID", "").startswith("24.04")
    python_ok = sys.version_info >= (3, 10)
    disk = shutil.disk_usage(target)
    cpu_count = os.cpu_count()
    memory_total = memory_available = None
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="ascii")
        values = {}
        for line in meminfo.splitlines():
            key, separator, raw = line.partition(":")
            parts = raw.split()
            if separator and len(parts) >= 2 and parts[1] == "kB":
                values[key] = int(parts[0]) * 1024
        memory_total = values.get("MemTotal")
        memory_available = values.get("MemAvailable")
    except (OSError, UnicodeError, ValueError):
        pass
    checks = {
        "linux": is_linux,
        "ubuntu_24_04": ubuntu_24,
        "arm64": is_arm64,
        "python_3_10_or_newer": python_ok,
        "git_available": shutil.which("git") is not None,
        "venv_available": __import__("importlib.util").util.find_spec("venv") is not None,
        "target_directory_exists": target.is_dir(),
        "target_directory_writable": os.access(target, os.W_OK),
    }
    return {
        "profile": "oracle_cloud_ubuntu_24_04_arm64",
        "read_only": True,
        "no_packages_installed": True,
        "no_network_requests": True,
        "platform": {
            "system": platform.system(),
            "distribution": release.get("PRETTY_NAME", "unknown"),
            "architecture": architecture,
            "python": platform.python_version(),
        },
        "resources": {
            "logical_cpu_count": cpu_count,
            "memory_total_bytes": memory_total,
            "memory_available_bytes": memory_available,
            "target_path": str(target),
            "target_disk_total_bytes": disk.total,
            "target_disk_free_bytes": disk.free,
        },
        "checks": checks,
        "ready": all(checks.values()),
        "notes": [
            "This is a host preflight, not a deployment or security certification.",
            "Resource values are observations; cgroup limits may reduce effective CPU or memory.",
            "No minimum RAM is imposed: Yuki's minimal profile is designed for CPU-only operation.",
            "Review OCI network rules and SSH access separately before changing firewall settings.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path.home(),
                        help="Existing directory whose filesystem will hold Yuki data")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)
    report = collect_report(target=args.target.expanduser())
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Yuki Oracle VPS preflight (read-only)")
        print(f"Platform: {report['platform']['distribution']} / {report['platform']['architecture']}")
        print(f"Python: {report['platform']['python']}")
        print(f"CPU: {report['resources']['logical_cpu_count'] or 'unknown'} logical CPUs")
        print(f"RAM: {report['resources']['memory_total_bytes'] or 'unknown'} total bytes; "
              f"{report['resources']['memory_available_bytes'] or 'unknown'} available bytes")
        print(f"Disk free at target: {report['resources']['target_disk_free_bytes']} bytes")
        for name, passed in report["checks"].items():
            print(f"{'PASS' if passed else 'CHECK'} {name}")
        print("READY" if report["ready"] else "NOT READY — review failed checks")
        for note in report["notes"]:
            print(f"NOTE: {note}")
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
