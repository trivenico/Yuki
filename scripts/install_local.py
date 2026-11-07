"""Create an isolated Python environment for the Yuki local browser alpha."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
ENV_DIR = ROOT / ".venv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable,
                        help="Python executable used to create the environment")
    parser.add_argument("--skip-install", action="store_true",
                        help="Create the environment but do not install packages")
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        print("Yuki requires Python 3.10 or newer.")
        return 1

    print(f"Creating isolated environment: {ENV_DIR}")
    try:
        venv.EnvBuilder(with_pip=True, clear=False).create(ENV_DIR)
    except (OSError, subprocess.SubprocessError) as exc:
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
