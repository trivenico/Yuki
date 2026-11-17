# Read-only desktop process inventory

Yuki's first process-monitoring foundation is intentionally observational.

## Setup

Install the optional security dependencies:

```bash
python -m pip install -r requirements-security.txt
```

## Use

```python
from defense.process_monitor import ReadOnlyProcessMonitor

monitor = ReadOnlyProcessMonitor(
    suspicious_names={"example-risky-tool.exe"},
)
snapshot = monitor.snapshot()
```

The watchlist is an exact, case-insensitive process-name match. It is a user
configured hint, not a malware verdict. Verify the process and its origin
before taking any action.

## Privacy and safety

- Reads only process ID, process display name, and process status.
- Does not collect command lines, environment variables, usernames, or
  executable paths.
- Does not persist snapshots or send process data over a network.
- Bounds each snapshot to at most 5,000 process records and the configured watchlist to 1,000 names.
- Does not terminate, suspend, inject into, or modify processes.
- Does not elevate privileges or request administrator access.
- Permission and provider failures produce a safe error; they do not trigger
  remediation.

This is an inventory primitive, not a complete endpoint detection product.
Future OS-specific sensors must be separately reviewed, opt-in, bounded,
observable, and read-only by default.
