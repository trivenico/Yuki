"""Static Python reachability audit for Yuki.

This is a conservative import/call-graph inventory, not a proof of runtime behavior.
It recognizes normal imports and relative imports, entrypoint roots, and literal
module references in the explicitly configured skill registry. Dynamic imports,
plugin loading, CLI routing, and external side effects need manual review.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
from collections import defaultdict, deque

DEFAULT_ROOTS = (
    "main.py",
    "yuki.py",
    "yuki_agent.py",
    "core/task_agent.py",
    "skills/registry.py",
)


def module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def resolve_imports(path: Path, root: Path, tree: ast.AST, known: set[str]) -> set[str]:
    current = module_name(path, root)
    package = current.split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    found.add(alias.name)
                elif alias.name.split(".")[0] in known:
                    found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = package[: max(0, len(package) - node.level + 1)]
                base = ".".join(prefix + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            candidates = [base] if base else []
            candidates.extend(f"{base}.{alias.name}" for alias in node.names if base)
            for candidate in candidates:
                if candidate in known:
                    found.add(candidate)
                elif candidate and candidate.split(".")[0] in known:
                    found.add(candidate.split(".")[0])
    return found


def audit(root: Path, entrypoints: tuple[str, ...] = DEFAULT_ROOTS) -> dict:
    root = root.resolve()
    files = sorted(root.rglob("*.py"))
    paths = {module_name(p, root): p for p in files}
    known = set(paths)
    edges: dict[str, set[str]] = defaultdict(set)
    parse_errors = []
    for mod, path in paths.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            parse_errors.append({"path": str(path.relative_to(root)), "error": str(exc)})
            continue
        edges[mod] = resolve_imports(path, root, tree, known)

    roots = set()
    for item in entrypoints:
        candidate = item.replace("/", ".").removesuffix(".py")
        if candidate in known:
            roots.add(candidate)
    for mod, path in paths.items():
        if mod.endswith("skills.registry") or mod.endswith("core.plugin_loader"):
            roots.add(mod)

    reachable = set(roots)
    queue = deque(roots)
    while queue:
        current = queue.popleft()
        for target in edges.get(current, ()):
            if target not in reachable:
                reachable.add(target)
                queue.append(target)

    def label(mod: str) -> str:
        path = paths[mod]
        if path.parts and "tests" in path.relative_to(root).parts:
            return "test-only"
        if mod in reachable:
            return "statically reachable"
        if path.name == "__init__.py":
            return "package/helper; inspect manually"
        if any(part in {"scripts", "tools", "migrations"} for part in path.relative_to(root).parts):
            return "standalone utility; inspect manually"
        return "not reached from configured static roots"

    records = []
    for mod, path in sorted(paths.items()):
        records.append({
            "module": mod,
            "path": str(path.relative_to(root)),
            "classification": label(mod),
            "imports": sorted(edges.get(mod, ())),
        })
    counts = defaultdict(int)
    for record in records:
        counts[record["classification"]] += 1
    return {
        "root": str(root),
        "entrypoints": sorted(roots),
        "python_files": len(files),
        "reachable_modules": len(reachable),
        "unreachable_or_manual_review": sum(
            1 for record in records
            if record["classification"] == "not reached from configured static roots"
        ),
        "classification_counts": dict(sorted(counts.items())),
        "parse_errors": parse_errors,
        "modules": records,
        "limitations": [
            "Static imports do not prove execution or product completeness.",
            "Dynamic imports, entrypoint scripts, plugins, reflection, CLI routing, and configuration-driven loading can be missed.",
            "Registry inclusion is not equivalent to a complete external integration.",
            "Review the generated JSON and validate candidate dead code manually before deleting anything.",
        ],
    }


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    output = audit(root)
    rendered = json.dumps(output, indent=2, sort_keys=True)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if not output["parse_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
