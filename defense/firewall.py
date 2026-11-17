"""Dry-run-only firewall planning with explicit human approval.

This release deliberately has no host firewall mutation implementation.
"""
from __future__ import annotations

import ipaddress
import platform
import uuid
from typing import Callable


class FirewallError(RuntimeError):
    """A firewall plan was invalid, unauthorized, or unavailable."""


def _validated_ip(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise FirewallError("Provide one literal IP address.")
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise FirewallError("Provide one literal IP address.") from exc
    if (ip.is_loopback or ip.is_multicast or ip.is_unspecified
            or ip.is_link_local or ip.is_reserved):
        raise FirewallError("This special-purpose IP address cannot be used.")
    return str(ip)


class FirewallManager:
    """Create bounded dry-run plans; never execute operating-system commands."""

    def __init__(self, *, enabled: bool = False,
                 approver: Callable[[str], bool] | None = None,
                 dry_run: bool = True, platform_name: str | None = None,
                 max_rules: int = 100):
        if enabled is not True and enabled is not False:
            raise ValueError("enabled must be a boolean")
        if dry_run is not True:
            raise FirewallError("Only dry-run firewall planning is available.")
        if approver is not None and not callable(approver):
            raise ValueError("approver must be callable")
        if (isinstance(max_rules, bool) or not isinstance(max_rules, int)
                or not 1 <= max_rules <= 1000):
            raise ValueError("max_rules must be an integer from 1 to 1000")
        selected_platform = platform_name or platform.system()
        if not isinstance(selected_platform, str) or not selected_platform:
            raise ValueError("platform_name must be a non-empty string")
        self.enabled = enabled
        self.approver = approver
        self.dry_run = True
        self.platform_name = selected_platform
        self.max_rules = max_rules
        self._rules: dict[str, tuple[str, str]] = {}

    def _approved(self, prompt: str) -> bool:
        if self.approver is None:
            return False
        try:
            return self.approver(prompt) is True
        except Exception as exc:
            raise FirewallError("Approval could not be verified.") from exc

    def block_temporary(self, address: str, *, seconds: int = 300) -> dict:
        ip = _validated_ip(address)
        if not self.enabled:
            raise FirewallError("Firewall planning is disabled.")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 30 <= seconds <= 3600:
            raise FirewallError("Temporary block must be 30-3600 seconds.")
        if len(self._rules) >= self.max_rules:
            raise FirewallError("The in-memory firewall plan limit has been reached.")
        prompt = f"Create a dry-run plan to temporarily block source IP {ip} for {seconds} seconds."
        if not self._approved(prompt):
            raise FirewallError("Explicit approval was not granted.")
        rule_id = "Yuki-" + uuid.uuid4().hex[:12]
        self._rules[rule_id] = (ip, "dry-run")
        return {
            "rule_id": rule_id,
            "ip": ip,
            "dry_run": True,
            "applied": False,
            "expires_in_seconds": seconds,
        }

    def remove(self, rule_id: str) -> bool:
        """Remove only a local dry-run plan; never changes the host firewall."""
        if not isinstance(rule_id, str) or len(rule_id) > 64:
            return False
        entry = self._rules.get(rule_id)
        if entry is None:
            return False
        ip, _mode = entry
        if not self._approved(f"Remove Yuki dry-run firewall plan {rule_id} for {ip}."):
            raise FirewallError("Explicit approval to remove the plan was not granted.")
        del self._rules[rule_id]
        return True
