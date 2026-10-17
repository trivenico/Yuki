"""Opt-in packet-based scan detector; alerts only and never changes firewall rules."""
from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic


class IDSError(RuntimeError):
    pass


class PortScanDetector:
    def __init__(self, *, threshold: int = 15, window_seconds: int = 10):
        if not 3 <= threshold <= 500 or not 1 <= window_seconds <= 120:
            raise ValueError("Invalid scan detector threshold/window.")
        self.threshold, self.window_seconds = threshold, window_seconds
        self._events = defaultdict(deque)
        self._alerted = set()

    def observe(self, source_ip: str, destination_port: int, *, timestamp: float | None = None) -> dict | None:
        import ipaddress
        try:
            source_ip = str(ipaddress.ip_address(source_ip))
        except ValueError as exc:
            raise ValueError("source_ip must be a literal IP address.") from exc
        if isinstance(destination_port, bool) or not isinstance(destination_port, int) or not 1 <= destination_port <= 65535:
            raise ValueError("destination_port must be from 1 to 65535.")
        now = monotonic() if timestamp is None else float(timestamp)
        events = self._events[source_ip]
        events.append((now, destination_port))
        while events and now - events[0][0] > self.window_seconds:
            events.popleft()
        unique_ports = {port for _, port in events}
        if len(unique_ports) >= self.threshold and source_ip not in self._alerted:
            self._alerted.add(source_ip)
            return {"type": "possible_port_scan", "source_ip": source_ip,
                    "unique_ports": len(unique_ports), "window_seconds": self.window_seconds,
                    "message": f"Possible port scan from {source_ip}; verify logs before taking action."}
        return None


class ScapyIDS:
    """Explicit packet capture wrapper. Import/install scapy only when invoked."""
    def __init__(self, detector: PortScanDetector | None = None):
        self.detector = detector or PortScanDetector()

    def sniff(self, *, seconds: int = 10, interface: str | None = None, callback=None):
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 60:
            raise ValueError("Capture duration must be 1-60 seconds.")
        try:
            from scapy.all import IP, TCP, sniff
        except ImportError as exc:
            raise IDSError("Install the optional scapy package.") from exc
        alerts = []
        def inspect(packet):
            if IP in packet and TCP in packet and int(packet[TCP].flags) & 0x02:
                alert = self.detector.observe(packet[IP].src, int(packet[TCP].dport))
                if alert:
                    alerts.append(alert)
                    if callback:
                        callback(alert)
        sniff(timeout=seconds, iface=interface, prn=inspect, store=False)
        return alerts
