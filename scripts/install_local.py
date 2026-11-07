"""Create an isolated Python environment for the Yuki local browser alpha."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ENV_DIR = ROOT / ".venv"


def _selected_python_supported(executable: str) -> bool:
    """Check the interpreter that will create the venv, not this script's Python."""
    try:
        result = subprocess.run(
            [
                executable, "-c",
                "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')",
            ],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        major, minor = (int(part) for part in result.stdout.strip().split(".", 1))
        return (major, minor) >= (3, 10)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable,
                        help="Python executable used to create the environment")
    parser.add_argument("--skip-install", action="store_true",
                        help="Create the environment but do not install packages")
    args = parser.parse_args()

    if not _selected_python_supported(args.python):
        print("The selected Python executable must be available and report Python 3.10 or newer.")
        return 1

    print(f"Creating isolated environment: {ENV_DIR}")
    try:
        subprocess.run([args.python, "-m", "venv", str(ENV_DIR)], cwd=ROOT, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Environment creation failed ({type(exc).__name__}).")
        return 1

    if os.name == "nt":
        python = ENV_DIR / "Scripts" / "python.exe"
    else:
        python = ENV_DIR / "bin" / "python"

    if not args.skip_install:
        requirements = ROOT / "requirements.txt"
        command = [str(python), "-m", "pip", "install", "--disable-pip-version-check",
                   "-r", str(requirements)]
        print("Installing Yuki runtime and browser dependencies…")
        try:
            subprocess.run(command, cwd=ROOT, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"Dependency installation failed (exit {exc.returncode}).")
            return exc.returncode or 1

    print()
    print("Yuki local environment is ready.")
    print("Next: run the platform launcher or:")
    print(f'  "{python}" scripts/launch_local.py')
    print("No model, API key, or external service was installed or configured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
