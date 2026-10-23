#!/usr/bin/env python3
"""Audit the repository's pip requirements files for duplicate declarations.

This intentionally uses only the standard library so the audit also runs before
optional dependencies are installed. It supports the simple package-specifier
format used by this repository; URL/VCS and pip option lines are reported as
unsupported rather than silently interpreted as package names.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT_FILES = sorted(ROOT.glob("requirements*.txt"))
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
NAME_NORMALIZER = re.compile(r"[-_.]+")


def normalize_name(name: str) -> str:
    return NAME_NORMALIZER.sub("-", name).lower()


def audit_files(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    declarations: dict[str, list[tuple[Path, int, str]]] = defaultdict(list)

    for path in paths:
        seen_in_file: dict[str, tuple[int, str]] = {}
        for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-", "git+", "http://", "https://")):
                errors.append(f"{path}:{line_number}: unsupported requirement directive/URL: {line}")
                continue
            # Strip inline comments only when introduced by whitespace, preserving URL fragments.
            line = re.split(r"\\s+#", line, maxsplit=1)[0].strip()
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\\[[^]]+\\])?\\s*(.*)$", line)
            if not match or not NAME_RE.fullmatch(match.group(1)):
                errors.append(f"{path}:{line_number}: unsupported requirement syntax: {line}")
                continue
            name = normalize_name(match.group(1))
            specifier = match.group(2).strip()
            if name in seen_in_file:
                first_line, first_specifier = seen_in_file[name]
                errors.append(
                    f"{path}:{line_number}: duplicate package '{name}' "
                    f"(first declared on line {first_line}: '{first_specifier}')"
                )
                continue
            seen_in_file[name] = (line_number, specifier)
            declarations[name].append((path, line_number, specifier))

    # A package may be repeated in separate optional manifests only if its
    # constraints agree exactly; otherwise installs can become order-dependent.
    for name, entries in sorted(declarations.items()):
        if len(entries) < 2:
            continue
        specs = {specifier for _, _, specifier in entries}
        if len(specs) > 1:
            details = ", ".join(f"{path.name}:{line} '{spec}'" for path, line, spec in entries)
            errors.append(f"conflicting constraints for '{name}' across manifests: {details}")

    return errors


def main() -> int:
    if not REQUIREMENT_FILES:
        print("No requirements*.txt manifests found.", file=sys.stderr)
        return 2
    errors = audit_files(REQUIREMENT_FILES)
    if errors:
        print("Dependency manifest audit failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Dependency manifest audit passed ({len(REQUIREMENT_FILES)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
