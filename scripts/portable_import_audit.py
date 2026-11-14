#!/usr/bin/env python3
"""Inventory static Python imports for portable-runtime dependency review.

This is an audit aid, not a dependency resolver: dynamic imports, entry-point
plugins, native shared libraries, and distribution-name aliases need separate
review. It never imports application modules or executes repository code.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "tests", "benchmarks",
    "scripts", "tools", "site-packages", "build", "dist",
}


def runtime_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS or part.startswith(".") for part in relative.parts):
            continue
        yield path


def local_roots(root: Path, files):
    roots = {path.stem for path in files if path.parent == root}
    roots.update(
        path.name for path in root.iterdir()
        if path.is_dir() and path.name not in EXCLUDED_DIRS
        and not path.name.startswith(".")
        and any(path.rglob("*.py"))
    )
    return roots


def audit_imports(root: Path):
    root = root.resolve()
    files = list(runtime_python_files(root))
    local = local_roots(root, files)
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    inventory = {"stdlib": {}, "local": {}, "third_party": {}}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ValueError(f"Cannot parse {path.relative_to(root)}: {exc}") from exc
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.append(node.module)
            for name in names:
                package = name.split(".", 1)[0]
                category = (
                    "stdlib" if package in stdlib else
                    "local" if package in local else
                    "third_party"
                )
                entry = inventory[category].setdefault(package, set())
                entry.add(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    return {
        category: {
            package: sorted(locations)
            for package, locations in sorted(packages.items())
        }
        for category, packages in inventory.items()
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        report = audit_imports(args.root)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for category, packages in report.items():
            print(f"[{category}]")
            for package, locations in packages.items():
                print(f"  {package}: {', '.join(locations)}")
        print("\nNote: third-party imports are import names, not PyPI distribution names.")
        print("Review lazy/dynamic imports, optional extras, plugins, and native libraries separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
