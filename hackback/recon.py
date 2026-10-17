"""Optional Nmap inventory for one explicitly authorized private lab host."""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
from typing import Callable


class ReconError(RuntimeError):
    pass


def validate_lab_target(target: str) -> str:
    try:
        address = ipaddress.ip_address(target.strip())
    except (ValueError, AttributeError) as exc:
        raise ReconError("Only a literal IP address is accepted; hostnames and CIDRs are blocked.") from exc
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        raise ReconError("Target must be a private, non-loopback lab IP address.")
    return str(address)


class NmapRecon:
    def __init__(self, *, enabled: bool = False, approver: Callable[[str], bool] | None = None,
                 nmap_binary: str = "nmap"):
        self.enabled = enabled
        self.approver = approver
        self.nmap_binary = nmap_binary

    def scan_common_tcp(self, target: str, *, timeout: int = 45) -> dict:
        if not self.enabled:
            raise ReconError("Recon is disabled by configuration.")
        target = validate_lab_target(target)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 5 <= timeout <= 120:
            raise ReconError("timeout must be between 5 and 120 seconds.")
        if self.approver is None or self.approver(
            f"Run a limited TCP connect scan of private lab host {target} (top 100 ports)."
        ) is not True:
            raise ReconError("Explicit authorization was not granted.")
        binary = shutil.which(self.nmap_binary)
        if not binary:
            raise ReconError("Nmap is not installed or not on PATH.")
        try:
            result = subprocess.run([binary, "-sT", "--top-ports", "100", "-T2", "-oN", "-", target],
                                    shell=False, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ReconError("Recon scan timed out.") from exc
        if result.returncode not in {0, 1}:
            raise ReconError("Nmap failed; raw process output was not trusted.")
        return {"target": target, "returncode": result.returncode,
                "output": result.stdout[-12000:], "errors": result.stderr[-2000:]}
