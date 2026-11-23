"""Standalone launcher for Yuki's optional Textual supervision dashboard."""
from __future__ import annotations

import sys

from core.diagnostics import record_diagnostic_event


def main() -> int:
    try:
        from ui.dashboard import run_dashboard
        record_diagnostic_event("dashboard", "started")
        run_dashboard()
    except RuntimeError as exc:
        record_diagnostic_event("dashboard", "startup_failed", exc)
        print(f"Yuki dashboard unavailable: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        record_diagnostic_event("dashboard", "interrupted")
        return 0
    except Exception as exc:
        record_diagnostic_event("dashboard", "runtime_failed", exc)
        print(f"Yuki dashboard failed ({type(exc).__name__}).", file=sys.stderr)
        return 2
    record_diagnostic_event("dashboard", "stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
