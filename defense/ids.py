"""Bounded, alert-only port-scan detection primitives.

The detector consumes caller-supplied connection metadata. It does not capture
packets, change firewall rules, or take response actions. State is bounded by
both source count and events per source.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from math import isfinite
from time import monotonic
import ipaddress


class IDSError(RuntimeError):
    pass


class PortScanDetector:
    """Detect many distinct destination ports from one source in a time window."""

    def __init__(self, *, threshold=15, window_seconds=10, max_sources=1024,
                 max_events_per_source=512):
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 3 <= threshold <= 500:
            raise ValueError("threshold must be 3-500")
        if isinstance(window_seconds, bool) or not isinstance(window_seconds, int) or not 1 <= window_seconds <= 120:
            raise ValueError("window_seconds must be 1-120")
        if isinstance(max_sources, bool) or not isinstance(max_sources, int) or not 1 <= max_sources <= 10000:
            raise ValueError("max_sources must be 1-10000")
        if (isinstance(max_events_per_source, bool) or not isinstance(max_events_per_source, int)
                or not threshold <= max_events_per_source <= 10000):
            raise ValueError("max_events_per_source must be threshold-10000")
        self.threshold = threshold
        self.window_seconds = window_seconds
        self.max_sources = max_sources
        self.max_events_per_source = max_events_per_source
        self._events = OrderedDict()
        self._alerted_until = {}

    @property
    def tracked_source_count(self):
        return len(self._events)

    def observe(self, source_ip, destination_port, *, timestamp=None):
        try:
            source_ip = str(ipaddress.ip_address(source_ip))
        except (ValueError, TypeError) as exc:
            raise ValueError("source_ip must be a literal IP") from exc
        if isinstance(destination_port, bool) or not isinstance(destination_port, int) or not 1 <= destination_port <= 65535:
            raise ValueError("destination_port must be 1-65535")
        now = monotonic() if timestamp is None else timestamp
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not isfinite(now):
            raise ValueError("timestamp must be a finite number")
        now = float(now)

        events = self._events.get(source_ip)
        if events is None:
            if len(self._events) >= self.max_sources:
                evicted, _ = self._events.popitem(last=False)
                self._alerted_until.pop(evicted, None)
            events = deque(maxlen=self.max_events_per_source)
            self._events[source_ip] = events
        else:
            if events and now < events[-1][0]:
                raise ValueError("timestamp must not move backwards for a source")
            self._events.move_to_end(source_ip)

        events.append((now, destination_port))
        cutoff = now - self.window_seconds
        while events and events[0][0] <= cutoff:
            events.popleft()

        if self._alerted_until.get(source_ip, float("-inf")) > now:
            return None
        unique_ports = {port for _, port in events}
        if len(unique_ports) >= self.threshold:
            self._alerted_until[source_ip] = now + self.window_seconds
            return {
                "type": "possible_port_scan",
                "source_ip": source_ip,
                "unique_ports": len(unique_ports),
                "window_seconds": self.window_seconds,
                "message": f"Possible port scan from {source_ip}; verify logs before action.",
            }
        return None


class PacketSniffer:
    """Finite Scapy capture, explicitly enabled by caller; alerts only."""

    def __init__(self, detector=None, *, enabled=False):
        self.detector = detector or PortScanDetector()
        self.enabled = enabled

    def sniff(self, *, interface=None, packet_limit=500, timeout=30, on_alert=None):
        if self.enabled is not True:
            raise IDSError("Packet sniffing is disabled.")
        if isinstance(packet_limit, bool) or not isinstance(packet_limit, int) or not 1 <= packet_limit <= 5000:
            raise ValueError("packet_limit must be 1-5000")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise ValueError("timeout must be 1-120 seconds")
        if on_alert is not None and not callable(on_alert):
            raise ValueError("on_alert must be callable")
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
            scapy_sniff(iface=interface, prn=inspect, store=False,
                        count=packet_limit, timeout=timeout)
        except PermissionError as exc:
            raise IDSError("Packet capture needs OS privileges and local authorization.") from exc
        except Exception as exc:
            raise IDSError("Packet capture failed safely.") from exc
        return alerts
