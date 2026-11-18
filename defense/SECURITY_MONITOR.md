# Unified desktop security monitor

`DesktopSecurityMonitor` is an opt-in facade for already-authorized, read-only sensors.

- Disabled by default; callers must explicitly enable it.
- Accepts injected process inventory and port-scan detector objects.
- Does not start background threads, open devices, or capture packets.
- Keeps only a bounded in-memory alert summary; disabling clears it.
- Does not persist telemetry, transmit it, terminate processes, or modify firewall rules.
- Network events must be supplied by an independently authorized caller.
- Alerts are evidence for human review, not proof of compromise.

The desktop UI must request sensor permissions separately and expose a persistent
monitoring indicator and immediate stop control before wiring any live sensor.
