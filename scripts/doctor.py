"""Check whether the current Python environment can run the Yuki prototype."""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REQUIRED_MODULES = (
    "yuki",
    "yuki_agent",
    "yuki_approvals",
    "yuki_events",
    "yuki_learning",
    "yuki_orchestrator",
    "yuki_planner",
    "yuki_providers",
    "yuki_security",
    "yuki_tasks",
    "yuki_tools",
    "yuki_workflow",
)


def main():
    print("Yuki environment diagnostics")
    print(f"Project root: {ROOT}")
    print(f"Python: {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        print("FAIL: Python 3.10 or newer is required.")
        return 1

    missing = [name for name in ("yuki.py", "requirements.txt", "tests") if not (ROOT / name).exists()]
    if missing:
        print("FAIL: Missing project paths: " + ", ".join(missing))
        return 1

    failures = []
    for module_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
            print(f"OK: {module_name}")
        except Exception as exc:
            failures.append((module_name, type(exc).__name__))
            print(f"FAIL: {module_name} ({type(exc).__name__})")

    if failures:
        print(f"Diagnostics failed: {len(failures)} module(s) could not load.")
        return 1
    print("PASS: Python version, project files, and core imports look ready.")
    print("Note: diagnostics do not verify voice, real AI providers, or browser automation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
