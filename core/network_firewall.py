"""Default-deny outbound network policy for Yuki's application transports.

This is an application-layer egress firewall. It complements, but does not
replace, host/cloud firewalls, inbound controls, TLS, or deployment hardening.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from threading import RLock
from typing import Callable, Iterable


_HOST = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$")


class FirewallPolicyError(ValueError):
    """Invalid trusted firewall policy configuration."""


@dataclass(frozen=True)
class FirewallDecision:
    allowed: bool
    reason: str
    host: str
    port: int
    protocol: str


class NetworkFirewall:
    """Immutable allow-list policy with audit-before-allow semantics.

    Hostnames are exact matches; wildcard suffixes and IP-literal host rules
    are intentionally unsupported. All DNS answers must be globally routable.
    """

    def __init__(
        self, *, allowed_hosts: Iterable[str], allowed_ports: Iterable[int] = (443,),
        allowed_protocols: Iterable[str] = ("tcp",),
        audit: Callable[[FirewallDecision], None] | None = None,
    ) -> None:
        if isinstance(allowed_hosts, (str, bytes)):
            raise FirewallPolicyError("allowed_hosts must be a collection of exact hostnames.")
        hosts = tuple(allowed_hosts)
        if not hosts or len(hosts) > 100:
            raise FirewallPolicyError("allowed_hosts must contain 1 to 100 hostnames.")
        normalized = set()
        for host in hosts:
            if not isinstance(host, str) or host != host.strip():
                raise FirewallPolicyError("invalid_allowed_host")
            candidate = host.lower().rstrip(".")
            if not _HOST.fullmatch(candidate):
                raise FirewallPolicyError("invalid_allowed_host")
            normalized.add(candidate)
        if len(normalized) != len(hosts):
            raise FirewallPolicyError("duplicate_allowed_host")
        ports = tuple(allowed_ports)
        if not ports or len(ports) > 32 or any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
            raise FirewallPolicyError("invalid_allowed_ports")
        protocols = tuple(allowed_protocols)
        if not protocols or len(protocols) > 8 or any(p not in {"tcp"} for p in protocols):
            raise FirewallPolicyError("invalid_allowed_protocols")
        if audit is not None and not callable(audit):
            raise FirewallPolicyError("audit_must_be_callable")
        self.allowed_hosts = frozenset(normalized)
        self.allowed_ports = frozenset(ports)
        self.allowed_protocols = frozenset(protocols)
        self._audit = audit
        self._lock = RLock()

    @staticmethod
    def _global_addresses(addresses: Iterable[str]) -> tuple[str, ...] | None:
        if isinstance(addresses, (str, bytes)):
            return None
        try:
            values = tuple(addresses)
        except TypeError:
            return None
        if not values or len(values) > 32:
            return None
        normalized = []
        for value in values:
            try:
                address = ipaddress.ip_address(value)
            except (ValueError, TypeError):
                return None
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if not address.is_global:
                return None
            normalized.append(str(address))
        return tuple(sorted(set(normalized)))

    def authorize(self, *, host: str, port: int, protocol: str = "tcp",
                  resolved_addresses: Iterable[str]) -> FirewallDecision:
        """Authorize a destination only after checking every DNS answer."""
        normalized_host = host.lower().rstrip(".") if isinstance(host, str) else ""
        addresses = self._global_addresses(resolved_addresses)
        if not _HOST.fullmatch(normalized_host):
            reason = "invalid_host"
        elif normalized_host not in self.allowed_hosts:
            reason = "host_not_allowlisted"
        elif type(port) is not int or port not in self.allowed_ports:
            reason = "port_not_allowlisted"
        elif protocol not in self.allowed_protocols:
            reason = "protocol_not_allowlisted"
        elif addresses is None:
            reason = "non_global_or_invalid_destination"
        else:
            reason = "allowed"
        decision = FirewallDecision(reason == "allowed", reason, normalized_host, port if type(port) is int else 0, protocol if isinstance(protocol, str) else "")
        if self._audit is not None:
            try:
                self._audit(decision)
            except Exception:
                return FirewallDecision(False, "audit_unavailable", decision.host, decision.port, decision.protocol)
        return decision
