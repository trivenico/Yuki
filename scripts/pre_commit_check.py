"""Dependency-light local checks; complements, but does not replace, CI."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def tracked_python_files():
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.py"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        paths = [ROOT / name for name in result.stdout.split("\0") if name]
        return [path for path in paths if path.is_file()]
    except (OSError, subprocess.CalledProcessError):
        return sorted(path for path in ROOT.rglob("*.py") if ".git" not in path.parts and ".venv" not in path.parts)


def main() -> int:
    errors = []
    for path in tracked_python_files():
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Pre-commit syntax checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
