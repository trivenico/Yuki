# Unified desktop security monitor

`DesktopSecurityMonitor` is an opt-in facade for authorized, read-only local sensors.

- Disabled by default; the desktop user must explicitly start monitoring for the current sign-in.
- The current desktop panel collects a bounded local process snapshot on Start and manual Refresh only; continuous/background monitoring is not implemented.
- Install the optional process-inspection dependency with `python -m pip install '.[security-monitoring]'`.
- Accepts an injected process inventory and optional port-scan detector.
- Does not start background threads, open packet-capture devices, or capture network traffic.
- Keeps only a bounded in-memory alert summary; disabling clears it.
- Does not persist telemetry, transmit it, terminate processes, or modify firewall rules.
- Network events must be supplied by a separately authorized caller; the desktop panel does not currently wire a network event source.
- Alerts are evidence for human review, not proof of compromise.

The desktop integration binds each service to the exact authenticated identity that
created it. All start, stop, and refresh operations run on a background worker, with a persistent session-status indicator.
Sign-out, session expiry, and application shutdown close the security service
before revoking the desktop session. Stale worker results are discarded.
