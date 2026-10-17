"""Opt-in bounded port-scan detector and packet capture; alerts only."""
from collections import defaultdict, deque
from time import monotonic
import ipaddress


class IDSError(RuntimeError):
    pass


class PortScanDetector:
    def __init__(self, *, threshold=15, window_seconds=10):
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 3 <= threshold <= 500:
            raise ValueError("threshold must be 3-500")
        if isinstance(window_seconds, bool) or not isinstance(window_seconds, int) or not 1 <= window_seconds <= 120:
            raise ValueError("window_seconds must be 1-120")
        self.threshold, self.window_seconds = threshold, window_seconds
        self._events = defaultdict(deque)
        self._alerted = set()

    def observe(self, source_ip, destination_port, *, timestamp=None):
        try:
            source_ip = str(ipaddress.ip_address(source_ip))
        except ValueError as exc:
            raise ValueError("source_ip must be a literal IP") from exc
        if isinstance(destination_port, bool) or not isinstance(destination_port, int) or not 1 <= destination_port <= 65535:
            raise ValueError("destination_port must be 1-65535")
        now = monotonic() if timestamp is None else float(timestamp)
        events = self._events[source_ip]
        events.append((now, destination_port))
        while events and now - events[0][0] > self.window_seconds:
            events.popleft()
        ports = {port for _, port in events}
        if len(ports) >= self.threshold and source_ip not in self._alerted:
            self._alerted.add(source_ip)
            return {"type": "possible_port_scan", "source_ip": source_ip, "unique_ports": len(ports),
                    "window_seconds": self.window_seconds,
                    "message": f"Possible port scan from {source_ip}; verify logs before action."}
        return None


class PacketSniffer:
    """Finite Scapy capture, explicitly enabled by the caller; never modifies firewall rules."""
    def __init__(self, detector=None, *, enabled=False):
        self.detector = detector or PortScanDetector()
        self.enabled = enabled

    def sniff(self, *, interface=None, packet_limit=500, timeout=30, on_alert=None):
        if not self.enabled:
            raise IDSError("Packet sniffing is disabled.")
        if isinstance(packet_limit, bool) or not isinstance(packet_limit, int) or not 1 <= packet_limit <= 5000:
            raise ValueError("packet_limit must be 1-5000")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise ValueError("timeout must be 1-120 seconds")
        try:
            from scapy.all import IP, TCP, sniff as scapy_sniff
        except ImportError as exc:
            raise IDSError("Install the optional Scapy package first.") from exc
        alerts = []
        def inspect(packet):
            if IP in packet and TCP in packet and int(packet[TCP].flags) & 0x02:
                alert = self.detector.observe(packet[IP].src, int(packet[TCP].dport))
                if alert:
                    alerts.append(alert)
                    if on_alert:
                        on_alert(alert)
        try:
            scapy_sniff(iface=interface, prn=inspect, store=False, count=packet_limit, timeout=timeout)
        except PermissionError as exc:
            raise IDSError("Packet capture needs OS privileges and local authorization.") from exc
        except Exception as exc:
            raise IDSError("Packet capture failed safely.") from exc
        return alerts
