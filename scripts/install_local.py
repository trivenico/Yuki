"""Create an isolated Python environment for the Yuki local browser alpha."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def _selected_python_supported(executable: str) -> bool:
    """Check the interpreter that will create the venv, not this script's Python."""
    try:
        result = subprocess.run(
            [executable, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        major, minor = (int(part) for part in result.stdout.strip().split(".", 1))
        return (major, minor) >= (3, 11)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable,
                        help="Python executable used to create the environment")
    parser.add_argument("--venv-dir", default=os.environ.get("YUKI_VENV_DIR", str(ROOT / ".venv")),
                        help="Environment directory (defaults to repository .venv)")
    parser.add_argument("--skip-install", action="store_true",
                        help="Create the environment but do not install packages")
    args = parser.parse_args()
    env_dir = Path(args.venv_dir).expanduser()
    if not env_dir.is_absolute():
        env_dir = (ROOT / env_dir).resolve()
    else:
        env_dir = env_dir.resolve()

    if not _selected_python_supported(args.python):
        print("The selected Python executable must be available and report Python 3.11 or newer.")
        return 1

    print(f"Creating isolated environment: {env_dir}")
    try:
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([args.python, "-m", "venv", str(env_dir)], cwd=ROOT, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Environment creation failed ({type(exc).__name__}).")
        return 1

    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not args.skip_install:
        command = [str(python), "-m", "pip", "install", "--disable-pip-version-check",
                   "-r", str(ROOT / "requirements.txt")]
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
