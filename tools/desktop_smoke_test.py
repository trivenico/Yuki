"""Run an installed or bundled Yuki Desktop executable in smoke-test mode."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def executable_for(bundle: Path) -> Path:
    if sys.platform == "darwin":
        app_bundle = Path(str(bundle) + ".app")
        candidates = [
            app_bundle / "Contents" / "MacOS" / "Yuki",
            bundle / "Contents" / "MacOS" / "Yuki",
        ]
    elif sys.platform == "win32":
        candidates = [bundle / "Yuki.exe"]
    else:
        candidates = [bundle / "Yuki"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Yuki Desktop executable not found in {bundle}")


def run_smoke_test(bundle: Path) -> None:
    executable = executable_for(bundle)
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run(
        [str(executable), "--smoke-test"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout)[-4000:]
        raise RuntimeError(
            f"Yuki Desktop smoke test failed with exit code {result.returncode}: {detail}"
        )


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m tools.desktop_smoke_test <bundle-directory>", file=sys.stderr)
        return 2
    run_smoke_test(Path(sys.argv[1]))
    print("Yuki Desktop packaged Qt runtime smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
