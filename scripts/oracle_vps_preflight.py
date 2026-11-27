#!/usr/bin/env python3
"""Read-only preflight for a Linux Yuki deployment (no installs or network calls)."""
from __future__ import annotations

import argparse
import json
import os
import importlib.util
import platform
import shutil
import sys
from pathlib import Path

from core.readiness import ResourceInventory


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
    try:
        disk = shutil.disk_usage(target)
        disk_total, disk_free = disk.total, disk.free
    except OSError:
        disk_total = disk_free = None
    inventory = ResourceInventory.detect()
    cpu_count = inventory.logical_cpu_count
    memory_total = inventory.memory_total_bytes
    memory_available = inventory.memory_available_bytes
    checks = {
        "linux": is_linux,
        "ubuntu_24_04": ubuntu_24,
        "arm64": is_arm64,
        "python_3_10_or_newer": python_ok,
        "git_available": shutil.which("git") is not None,
        "venv_available": importlib.util.find_spec("venv") is not None,
        "target_directory_exists": target.is_dir(),
        "target_directory_writable": os.access(target, os.W_OK),
        "target_disk_capacity_available": disk_total is not None and disk_free is not None,
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
            "effective_cpu_count": inventory.effective_cpu_count,
            "cpu_cgroup_limit": inventory.cpu_cgroup_limit,
            "cpu_cgroup_source": inventory.cpu_cgroup_source,
            "memory_total_bytes": memory_total,
            "memory_available_bytes": memory_available,
            "memory_cgroup_limit_bytes": inventory.memory_cgroup_limit_bytes,
            "memory_cgroup_available_bytes": inventory.memory_cgroup_available_bytes,
            "memory_cgroup_source": inventory.memory_cgroup_source,
            "memory_effective_available_bytes": inventory.memory_effective_available_bytes,
            "target_path": str(target),
            "target_disk_total_bytes": disk_total,
            "target_disk_free_bytes": disk_free,
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
        resources = report["resources"]
        print(f"CPU: {resources['logical_cpu_count'] or 'unknown'} logical; "
              f"{resources['effective_cpu_count'] or 'unknown'} effective CPUs "
              f"(cgroup: {resources['cpu_cgroup_source']})")
        print(f"RAM: {resources['memory_total_bytes'] or 'unknown'} host total bytes; "
              f"{resources['memory_effective_available_bytes'] or 'unknown'} effective available bytes "
              f"(cgroup: {resources['memory_cgroup_source']})")
        disk_free = report["resources"]["target_disk_free_bytes"]
        print(f"Disk free at target: {disk_free if disk_free is not None else 'unavailable'} bytes")
        for name, passed in report["checks"].items():
            print(f"{'PASS' if passed else 'CHECK'} {name}")
        print("READY" if report["ready"] else "NOT READY — review failed checks")
        for note in report["notes"]:
            print(f"NOTE: {note}")
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
