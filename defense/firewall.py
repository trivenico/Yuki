"""Human-approved temporary host firewall blocks for a single validated IP.

Firewall modification is disabled by default. This module never adds rules without
an explicit approver callback. Prefer an OS-native least-privilege service account.
"""
from __future__ import annotations

import ipaddress
import platform
import shutil
import subprocess
import uuid
from typing import Callable


class FirewallError(RuntimeError):
    pass


def _validated_ip(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value.strip())
    except (ValueError, AttributeError) as exc:
        raise FirewallError("Provide one literal IP address.") from exc
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified:
        raise FirewallError("Loopback, multicast, and unspecified addresses cannot be blocked.")
    return str(ip)


class FirewallManager:
    def __init__(self, *, enabled: bool = False, approver: Callable[[str], bool] | None = None,
                 dry_run: bool = True, platform_name: str | None = None):
        self.enabled, self.approver, self.dry_run = enabled, approver, dry_run
        self.platform_name = platform_name or platform.system()
        self._rules: dict[str, tuple[str, str]] = {}

    def block_temporary(self, address: str, *, seconds: int = 300) -> dict:
        ip = _validated_ip(address)
        if not self.enabled:
            raise FirewallError("Firewall changes are disabled.")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 30 <= seconds <= 3600:
            raise FirewallError("Temporary block must be 30-3600 seconds.")
        if self.approver is None or self.approver(f"Temporarily block source IP {ip} for {seconds} seconds.") is not True:
            raise FirewallError("Explicit approval was not granted.")
        rule_id = "Yuki-" + uuid.uuid4().hex[:12]
        if self.dry_run:
            self._rules[rule_id] = (ip, "dry-run")
            return {"rule_id": rule_id, "ip": ip, "dry_run": True, "expires_in_seconds": seconds}
        if self.platform_name == "Linux":
            binary = shutil.which("iptables")
            if not binary:
                raise FirewallError("iptables is not installed.")
            command = [binary, "-I", "INPUT", "1", "-s", ip, "-m", "comment", "--comment", rule_id, "-j", "DROP"]
        elif self.platform_name == "Windows":
            binary = shutil.which("netsh")
            if not binary:
                raise FirewallError("netsh is unavailable.")
            command = [binary, "advfirewall", "firewall", "add", "rule", f"name={rule_id}",
                       "dir=in", "action=block", f"remoteip={ip}"]
        else:
            raise FirewallError("This OS is not supported.")
        try:
            result = subprocess.run(command, shell=False, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FirewallError("Firewall command failed or timed out.") from exc
        if result.returncode:
            raise FirewallError("Firewall rejected the rule; no success was reported.")
        self._rules[rule_id] = (ip, self.platform_name)
        return {"rule_id": rule_id, "ip": ip, "dry_run": False, "expires_in_seconds": seconds}

    def remove(self, rule_id: str) -> bool:
        entry = self._rules.get(rule_id)
        if not entry:
            return False
        ip, mode = entry
        if mode == "dry-run":
            del self._rules[rule_id]
            return True
        if self.approver is None or self.approver(f"Remove Yuki firewall rule {rule_id} for {ip}.") is not True:
            raise FirewallError("Explicit approval to remove rule was not granted.")
        if mode == "Linux":
            binary = shutil.which("iptables")
            command = [binary, "-D", "INPUT", "-s", ip, "-m", "comment", "--comment", rule_id, "-j", "DROP"] if binary else []
        elif mode == "Windows":
            binary = shutil.which("netsh")
            command = [binary, "advfirewall", "firewall", "delete", "rule", f"name={rule_id}"] if binary else []
        else:
            return False
        if not command:
            raise FirewallError("Firewall utility is unavailable.")
        result = subprocess.run(command, shell=False, capture_output=True, text=True, timeout=10, check=False)
        if result.returncode:
            raise FirewallError("Firewall rule removal failed.")
        del self._rules[rule_id]
        return True
