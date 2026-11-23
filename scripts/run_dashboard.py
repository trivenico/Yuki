"""Standalone launcher for Yuki's optional Textual supervision dashboard."""
from __future__ import annotations

import sys


def main() -> int:
    try:
        from ui.dashboard import run_dashboard
        run_dashboard()
    except RuntimeError as exc:
        print(f"Yuki dashboard unavailable: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
