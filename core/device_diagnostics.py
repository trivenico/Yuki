"""Read-only local device inventory and basic diagnostics.

This module never reads file contents, accesses phone data, changes mounts, or repairs
filesystems. Platform-specific inventory is best-effort and missing data stays unknown.
"""
from __future__ import annotations

import os
import platform
import shutil
from typing import Any


def _linux_usb_devices(root: str = "/sys/bus/usb/devices") -> list[dict[str, Any]]:
    """Return non-sensitive USB identity fields from Linux sysfs (never serial numbers)."""
    if platform.system().lower() != "linux" or not os.path.isdir(root):
        return []
    devices: list[dict[str, Any]] = []
    try:
        entries = sorted(os.scandir(root), key=lambda entry: entry.name)
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir(follow_symlinks=True):
            continue
        try:
            with open(os.path.join(entry.path, "idVendor"), encoding="ascii") as stream:
                vendor_id = stream.read(64).strip()
            with open(os.path.join(entry.path, "idProduct"), encoding="ascii") as stream:
                product_id = stream.read(64).strip()
        except (OSError, ValueError):
            # USB interface nodes and hubs without identity files are not device records.
            continue
        if not vendor_id or not product_id:
            continue
        record: dict[str, Any] = {
            "kind": "usb",
            "sysfs_name": entry.name,
            "vendor_id": vendor_id.lower(),
            "product_id": product_id.lower(),
            "manufacturer": None,
            "product": None,
            "source": "linux_sysfs",
        }
        for field in ("manufacturer", "product"):
            try:
                with open(os.path.join(entry.path, field), encoding="utf-8", errors="replace") as stream:
                    value = stream.read(256).strip()
                record[field] = value or None
            except OSError:
                pass
        devices.append(record)
    return devices


def _mounted_volumes() -> list[dict[str, Any]]:
    """List mounted volumes using optional psutil; do not walk or open user files."""
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return []
    try:
        partitions = psutil.disk_partitions(all=False)
    except (OSError, RuntimeError):
        return []
    volumes: list[dict[str, Any]] = []
    for partition in partitions:
        mountpoint = getattr(partition, "mountpoint", None)
        if not isinstance(mountpoint, str) or not mountpoint:
            continue
        volume: dict[str, Any] = {
            "kind": "volume",
            "mountpoint": mountpoint,
            "filesystem": getattr(partition, "fstype", None) or None,
            "device": None,
            "read_only": "ro" in str(getattr(partition, "opts", "")).split(","),
            "total_bytes": None,
            "free_bytes": None,
            "used_percent": None,
            "source": "psutil",
        }
        # Avoid exposing raw device paths by default; report only filesystem telemetry.
        try:
            usage = shutil.disk_usage(mountpoint)
            volume["total_bytes"] = usage.total
            volume["free_bytes"] = usage.free
            if usage.total > 0:
                volume["used_percent"] = round((usage.total - usage.free) * 100 / usage.total, 1)
        except (OSError, PermissionError):
            volume["usage_error"] = "Volume usage unavailable or access denied."
        volumes.append(volume)
    return volumes


def discover_devices() -> dict[str, Any]:
    """Discover safe metadata for locally visible USB devices and mounted volumes."""
    system = platform.system() or "unknown"
    usb = _linux_usb_devices()
    volumes = _mounted_volumes()
    return {
        "ok": True,
        "platform": system,
        "read_only": True,
        "privacy": "No file contents or USB serial numbers are collected.",
        "capabilities": {
            "usb_identity": "available" if system.lower() == "linux" else "unsupported",
            "mounted_volume_usage": "available" if volumes else "unavailable",
        },
        "devices": usb + volumes,
    }


def format_device_report(inventory: dict[str, Any] | None = None) -> str:
    """Format an operator-readable report; preserve unknown/unavailable distinctions."""
    data = inventory if inventory is not None else discover_devices()
    lines = [
        f"Device diagnostics (platform: {data.get('platform', 'unknown')})",
        "Mode: read-only; no file contents inspected; no changes made.",
    ]
    devices = data.get("devices")
    if not isinstance(devices, list) or not devices:
        lines.append("No supported USB identity or mounted-volume records available.")
    for item in devices:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "usb":
            name = item.get("product") or "USB device"
            manufacturer = item.get("manufacturer")
            if manufacturer:
                name = f"{manufacturer} {name}"
            lines.append(
                f"USB: {name} (vendor {item.get('vendor_id', 'unknown')}, "
                f"product {item.get('product_id', 'unknown')}; "
                f"ID {item.get('sysfs_name', 'unknown')})"
            )
        elif item.get("kind") == "volume":
            mount = item.get("mountpoint", "unknown mount")
            filesystem = item.get("filesystem") or "unknown filesystem"
            total = item.get("total_bytes")
            free = item.get("free_bytes")
            used = item.get("used_percent")
            if isinstance(total, int) and isinstance(free, int):
                capacity = f"{total / (1024 ** 3):.1f} GiB total, {free / (1024 ** 3):.1f} GiB free"
            else:
                capacity = "capacity unavailable"
            used_text = f", {used:.1f}% used" if isinstance(used, (int, float)) else ""
            readonly = ", read-only" if item.get("read_only") else ""
            lines.append(f"Volume: {mount} ({filesystem}; {capacity}{used_text}{readonly})")
            if item.get("usage_error"):
                lines.append(f"  Note: {item['usage_error']}")
    lines.append("Note: This inventory does not verify drive health, SMART status, or filesystem integrity.")
    return "\n".join(lines)
