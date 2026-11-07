"""Print read-only local hardware and runtime capability diagnostics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.readiness import build_readiness_report, recommended_worker_concurrency


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="Workspace volume to inspect for free disk space.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)
    report = build_readiness_report(workspace_path=args.workspace)
    data = report.to_dict()
    data["recommended_worker_count"] = recommended_worker_concurrency()
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"Yuki hardware profile — {report.operating_system} / {report.architecture}")
        for name, capability in report.capabilities.items():
            print(f"[{capability.status.upper()}] {name}: {capability.detail}")
        print(f"Recommended active worker count: {data['recommended_worker_count']}")
        print("Recommendations are conservative estimates, not model-fit guarantees.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
