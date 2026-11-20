"""Read-only process network-namespace observations; never attestation or connectivity proof."""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from core.linux_host_evidence import _parse_start_time
from core.sandbox_runtime import SandboxRuntimeError

_MAX_FILE = 262_144
_MAX_INTERFACES = 256
_MAX_ROUTES = 1024
_MAX_LISTENERS = 4096
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
_HEX8 = re.compile(r"^[0-9a-fA-F]{8}$")
_HEX4 = re.compile(r"^[0-9a-fA-F]{4}$")


@dataclass(frozen=True)
class LinuxInterfaceObservation:
    name: str
    rx_bytes: int
    tx_bytes: int


@dataclass(frozen=True)
class LinuxIpv4Route:
    interface: str
    destination_hex: str
    gateway_hex: str
    flags: int
    is_default: bool


@dataclass(frozen=True)
class LinuxTcpListener:
    local_address: str
    local_port: int


@dataclass(frozen=True)
class LinuxNetworkSnapshot:
    pid: int
    start_time_ticks: int
    interfaces: tuple[LinuxInterfaceObservation, ...]
    ipv4_routes: tuple[LinuxIpv4Route, ...]
    tcp_listeners: tuple[LinuxTcpListener, ...]
    has_up_default_route: bool
    observed_sources: tuple[str, ...]
    unknown_controls: tuple[str, ...]
    evidence_class: str = "diagnostic_only"
    attested: bool = False
    verified: bool = False


def _read_proc_net(path: Path, *, required: bool = True) -> str | None:
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SandboxRuntimeError("network namespace observation is not a regular file")
        payload = bytearray()
        while len(payload) <= _MAX_FILE:
            chunk = os.read(descriptor, min(8192, _MAX_FILE + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > _MAX_FILE:
            raise SandboxRuntimeError("network namespace observation exceeds size limit")
        try:
            return bytes(payload).decode("ascii", errors="strict")
        except UnicodeError as exc:
            raise SandboxRuntimeError("network namespace observation is not ASCII") from exc
    except FileNotFoundError:
        if not required:
            return None
        raise SandboxRuntimeError("required network namespace observation is unavailable")
    except SandboxRuntimeError:
        raise
    except OSError as exc:
        raise SandboxRuntimeError("network namespace observation is unavailable or unreadable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _parse_interfaces(payload: str) -> tuple[LinuxInterfaceObservation, ...]:
    result = []
    for line in payload.splitlines()[2:]:
        if not line.strip():
            continue
        if ":" not in line:
            raise SandboxRuntimeError("network interface counters are malformed")
        name, counters = line.split(":", 1)
        name = name.strip()
        fields = counters.split()
        if not _IFACE_RE.fullmatch(name) or len(fields) != 16:
            raise SandboxRuntimeError("network interface record has invalid fields")
        if any(not item.isascii() or not item.isdecimal() for item in fields):
            raise SandboxRuntimeError("network interface counters are not unsigned integers")
        result.append(LinuxInterfaceObservation(name, int(fields[0]), int(fields[8])))
        if len(result) > _MAX_INTERFACES:
            raise SandboxRuntimeError("network interface count exceeds limit")
    if len({item.name for item in result}) != len(result):
        raise SandboxRuntimeError("network interface list contains duplicates")
    return tuple(result)


def _parse_routes(payload: str) -> tuple[LinuxIpv4Route, ...]:
    lines = payload.splitlines()
    if not lines or not lines[0].startswith("Iface"):
        raise SandboxRuntimeError("IPv4 route table header is malformed")
    routes = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 11 or not _IFACE_RE.fullmatch(fields[0]):
            raise SandboxRuntimeError("IPv4 route record has malformed fields")
        if (not _HEX8.fullmatch(fields[1]) or not _HEX8.fullmatch(fields[2])
                or not _HEX4.fullmatch(fields[3]) or not _HEX8.fullmatch(fields[7])):
            raise SandboxRuntimeError("IPv4 route record has malformed hexadecimal fields")
        if any(not fields[index].isascii() or not fields[index].isdecimal()
               for index in (4, 5, 6, 8, 9, 10)):
            raise SandboxRuntimeError("IPv4 route metrics are malformed")
        flags = int(fields[3], 16)
        routes.append(LinuxIpv4Route(
            interface=fields[0],
            destination_hex=fields[1].lower(),
            gateway_hex=fields[2].lower(),
            flags=flags,
            is_default=fields[1].lower() == "00000000" and fields[7].lower() == "00000000",
        ))
        if len(routes) > _MAX_ROUTES:
            raise SandboxRuntimeError("IPv4 route count exceeds limit")
    return tuple(routes)


def _ipv4_from_proc_hex(value: str) -> str:
    raw = bytes.fromhex(value)
    return ".".join(str(part) for part in raw[::-1])


def _parse_tcp_listeners(payload: str) -> tuple[LinuxTcpListener, ...]:
    lines = payload.splitlines()
    if not lines or not lines[0].lstrip().startswith("sl"):
        raise SandboxRuntimeError("TCP socket table header is malformed")
    listeners = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 10 or not fields[0].endswith(":"):
            raise SandboxRuntimeError("TCP socket record has malformed fields")
        local = fields[1].split(":")
        if len(local) != 2 or not _HEX8.fullmatch(local[0]) or not _HEX4.fullmatch(local[1]):
            raise SandboxRuntimeError("TCP local endpoint is malformed")
        if not _HEX2.fullmatch(fields[3]):
            raise SandboxRuntimeError("TCP socket state is malformed")
        if fields[3].lower() == "0a":
            listeners.append(LinuxTcpListener(_ipv4_from_proc_hex(local[0]), int(local[1], 16)))
        if len(listeners) > _MAX_LISTENERS:
            raise SandboxRuntimeError("TCP listener count exceeds limit")
    return tuple(listeners)


_HEX2 = re.compile(r"^[0-9a-fA-F]{2}$")


def collect_linux_network_snapshot(pid: int, *, proc_root: str = "/proc") -> LinuxNetworkSnapshot:
    """Observe /proc/<pid>/net tables; no packet probes or egress verification occur."""
    if os.name != "posix":
        raise SandboxRuntimeError("Linux network namespace observations require a POSIX host")
    if type(pid) is not int or pid <= 0:
        raise SandboxRuntimeError("process ID must be a positive integer")
    if type(proc_root) is not str or not proc_root or not os.path.isabs(proc_root):
        raise SandboxRuntimeError("procfs root must be an absolute path")
    root = Path(proc_root)
    try:
        root_info = root.lstat()
        process = root / str(pid)
        process_info = process.lstat()
        net_info = (process / "net").lstat()
    except OSError as exc:
        raise SandboxRuntimeError("process network namespace path is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise SandboxRuntimeError("procfs root must be a non-symlink directory")
    if stat.S_ISLNK(process_info.st_mode) or not stat.S_ISDIR(process_info.st_mode):
        raise SandboxRuntimeError("process entry must be a non-symlink directory")
    if stat.S_ISLNK(net_info.st_mode) or not stat.S_ISDIR(net_info.st_mode):
        raise SandboxRuntimeError("process network entry must be a non-symlink directory")

    first_start = _parse_start_time(_read_proc_net(process / "stat"))
    net = process / "net"
    interfaces = _parse_interfaces(_read_proc_net(net / "dev"))
    routes = _parse_routes(_read_proc_net(net / "route"))
    tcp = _parse_tcp_listeners(_read_proc_net(net / "tcp"))
    second_start = _parse_start_time(_read_proc_net(process / "stat"))
    if first_start != second_start:
        raise SandboxRuntimeError("process identity changed during network observation")

    return LinuxNetworkSnapshot(
        pid=pid,
        start_time_ticks=first_start,
        interfaces=interfaces,
        ipv4_routes=routes,
        tcp_listeners=tcp,
        has_up_default_route=any(route.is_default and route.flags & 1 for route in routes),
        observed_sources=("procfs:net/dev", "procfs:net/route", "procfs:net/tcp", "procfs:stat"),
        unknown_controls=(
            "procfs root and process identity are caller supplied and not independently attested",
            "route presence does not prove reachability or permitted egress",
            "absence of an IPv4 default route does not prove absence of IPv6 or other network paths",
            "loopback, Unix sockets, raw sockets, netfilter, DNS, proxy, and host services are not tested",
            "TCP listener table is an instantaneous IPv4-only view and does not inspect UDP or TCP6",
            "interfaces and routes may change immediately after observation",
            "network namespace identity is not compared to a trusted host baseline",
            "cgroup, mount, LSM, seccomp, device, and host-secret isolation remain unverified",
        ),
        evidence_class="diagnostic_only",
        attested=False,
        verified=False,
    )
