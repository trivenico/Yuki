"""Static repository wiring audit.

This is a heuristic inventory, not a proof of runtime reachability: dynamic imports,
entry-point metadata, plugins and external launchers may not appear as imports.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
}
MAX_FILE_BYTES = 1024 * 1024


def audit_python_wiring(root: str | Path) -> dict:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError("Audit root must be an existing directory.")

    files = []
    for path in base.rglob("*.py"):
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        files.append(path)
    files.sort(key=lambda path: path.as_posix())

    module_by_path = {}
    for path in files:
        rel = path.relative_to(base).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            module_by_path[path] = ".".join(parts)

    module_to_path = {module: path for path, module in module_by_path.items()}
    inbound = defaultdict(set)
    failures = []
    source_kinds = {}
    imports_by_path = {}

    for path in files:
        rel = path.relative_to(base)
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                failures.append({"path": rel.as_posix(), "reason": "SourceTooLarge"})
                source_kinds[path] = "unreadable"
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(rel))
        except (OSError, UnicodeError, SyntaxError) as exc:
            failures.append({"path": rel.as_posix(), "reason": type(exc).__name__})
            source_kinds[path] = "unreadable"
            continue

        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
                elif node.level:
                    package = module_by_path.get(path, "").split(".")[:-1]
                    imports.add(".".join(package[:max(0, len(package) - node.level + 1)]))
        imports_by_path[path] = imports
        name = path.name
        if name.startswith("test_") or "tests" in rel.parts:
            source_kinds[path] = "test"
        elif "scripts" in rel.parts or "examples" in rel.parts:
            source_kinds[path] = "script_or_example"
        elif name in {"__main__.py", "main.py", "yuki.py", "app.py"}:
            source_kinds[path] = "possible_entrypoint"
        else:
            source_kinds[path] = "module"

    for source_path, imports in imports_by_path.items():
        source_module = module_by_path.get(source_path, source_path.stem)
        for imported in imports:
            candidate = imported
            while candidate:
                target = module_to_path.get(candidate)
                if target and target != source_path:
                    inbound[target].add(source_path.relative_to(base).as_posix())
                    break
                candidate = candidate.rpartition(".")[0]

    records = []
    for path in files:
        rel = path.relative_to(base).as_posix()
        kind = source_kinds.get(path, "unreadable")
        callers = sorted(inbound.get(path, set()))
        if kind in {"test", "script_or_example"}:
            status = "test_or_example"
        elif kind == "possible_entrypoint":
            status = "possible_entrypoint"
        elif kind == "unreadable":
            status = "needs_inspection"
        elif callers:
            status = "statically_imported"
        else:
            status = "no_static_inbound_import"
        records.append({
            "path": rel,
            "kind": kind,
            "status": status,
            "inbound_import_count": len(callers),
            "inbound_importers": callers[:20],
        })

    counts = defaultdict(int)
    for record in records:
        counts[record["status"]] += 1
    return {
        "root": str(base),
        "python_file_count": len(files),
        "counts": dict(sorted(counts.items())),
        "parse_or_size_issues": failures,
        "files": records,
        "limitations": [
            "No static inbound import is a review flag, not proof that a file is unused.",
            "Dynamic imports, plugin discovery, entry points, reflection, and external launchers may not be visible.",
            "Non-Python files and runtime task/capability wiring are outside this audit.",
        ],
    }
