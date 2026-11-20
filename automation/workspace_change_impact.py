"""Read-only, AST-based impact hints for proposed workspace changes.

The analyzer consumes caller-supplied source snapshots. It never reads files,
imports project modules, executes code, runs tests, or applies a proposal.
Results are deterministic hints and always require human review.
"""
from __future__ import annotations

import ast
import hashlib
import json
from collections import deque
from dataclasses import dataclass
import re
from typing import Mapping

from automation.workspace_change_proposal import WorkspaceChangeProposal

_MAX_SOURCE_FILES = 5000
_MAX_SOURCE_FILE_BYTES = 256 * 1024
_MAX_TOTAL_SOURCE_BYTES = 8 * 1024 * 1024
_MAX_IMPORTERS = 500
_SENSITIVE_PATH = re.compile(
    r"(^|/)(auth|security|crypto|cryptography|permission|policy|approval|secret|credential|identity|sandbox)(/|_|\.|$)",
    re.I,
)
_CONFIG_PATH = re.compile(
    r"(^|/)([^/]*\.(toml|yaml|yml|json|ini|cfg)|Dockerfile|Makefile|requirements[^/]*\.txt)$",
    re.I,
)
_DEPENDENCY_PATH = re.compile(
    r"(^|/)(pyproject\.toml|requirements[^/]*\.txt|Pipfile|Pipfile\.lock|poetry\.lock|uv\.lock|setup\.py|setup\.cfg)$",
    re.I,
)


@dataclass(frozen=True)
class WorkspaceChangeImpact:
    changed_paths: tuple[str, ...]
    changed_python_modules: tuple[str, ...]
    direct_importers: tuple[str, ...]
    transitive_importers: tuple[str, ...]
    test_candidates: tuple[str, ...]
    configuration_paths: tuple[str, ...]
    dependency_paths: tuple[str, ...]
    security_sensitive_paths: tuple[str, ...]
    parse_errors: tuple[str, ...]
    analyzed_python_files: int
    requires_human_review: bool = True
    tests_executed: bool = False


def _module_name(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or any(not part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def _imports(source: str, path: str) -> set[str]:
    tree = ast.parse(source, filename=path)
    found: set[str] = set()
    package = _module_name(path)
    if path.endswith("/__init__.py"):
        package_parts = (package or "").split(".") if package else []
    else:
        package_parts = (package or "").split(".")[:-1] if package else []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base_parts = package_parts[:]
            if node.level:
                trim = node.level - 1
                if trim > len(base_parts):
                    continue
                base_parts = base_parts[:len(base_parts) - trim]
            if node.module:
                base_parts.extend(node.module.split("."))
            if base_parts:
                base = ".".join(base_parts)
                found.add(base)
                for alias in node.names:
                    if alias.name != "*":
                        found.add(f"{base}.{alias.name}")
    return found


def analyze_workspace_change_impact(
    proposal: WorkspaceChangeProposal,
    project_sources: Mapping[str, str],
) -> WorkspaceChangeImpact:
    """Analyze importers and likely test targets using bounded source snapshots."""
    if not isinstance(proposal, WorkspaceChangeProposal):
        raise TypeError("proposal must be a WorkspaceChangeProposal.")
    if not isinstance(project_sources, Mapping):
        raise TypeError("project_sources must be a path-to-text mapping.")
    if len(project_sources) > _MAX_SOURCE_FILES:
        raise ValueError("source snapshot exceeds the file-count limit.")

    if not isinstance(proposal.changes, tuple) or not proposal.changes:
        raise ValueError("proposal changes must be a non-empty immutable tuple.")
    digest_items = []
    for change in proposal.changes:
        if not isinstance(change.path, str) or not isinstance(change.content, str):
            raise ValueError("proposal changes must contain valid path and text content.")
        if "\x00" in change.content:
            raise ValueError("proposed source content must not contain NUL bytes.")
        digest_items.append({
            "path": change.path,
            "expected_sha256": change.expected_sha256,
            "new_sha256": hashlib.sha256(change.content.encode("utf-8")).hexdigest(),
        })
    actual_digest = hashlib.sha256(
        json.dumps(digest_items, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if actual_digest != proposal.content_digest:
        raise ValueError("proposal content digest does not match its file changes.")

    sources = dict(project_sources)
    for change in proposal.changes:
        sources[change.path] = change.content
    if len(sources) > _MAX_SOURCE_FILES:
        raise ValueError("source snapshot plus proposal exceeds the file-count limit.")

    total_bytes = 0
    for path, source in sources.items():
        if not isinstance(path, str) or not path or "\x00" in path:
            raise ValueError("source paths must be non-empty text.")
        if not isinstance(source, str) or "\x00" in source:
            raise ValueError("source snapshots must contain text without NUL bytes.")
        size = len(source.encode("utf-8"))
        if size > _MAX_SOURCE_FILE_BYTES:
            raise ValueError("a source snapshot file exceeds the size limit.")
        total_bytes += size
        if total_bytes > _MAX_TOTAL_SOURCE_BYTES:
            raise ValueError("combined source snapshot exceeds the size limit.")

    module_to_path = {
        module: path for path in sources
        if (module := _module_name(path)) is not None
    }
    imports_by_path: dict[str, set[str]] = {}
    parse_errors: list[str] = []
    for path, source in sources.items():
        if not path.endswith(".py"):
            continue
        try:
            imports_by_path[path] = _imports(source, path)
        except (SyntaxError, ValueError):
            parse_errors.append(path)
            imports_by_path[path] = set()

    changed_paths = tuple(change.path for change in proposal.changes)
    changed_modules = tuple(sorted({
        module for path in changed_paths
        if (module := _module_name(path)) is not None
    }))
    changed_module_set = set(changed_modules)
    reverse_edges: dict[str, set[str]] = {}
    for importer, imported_names in imports_by_path.items():
        for imported in imported_names:
            target = imported
            while target and target not in module_to_path:
                target = target.rpartition(".")[0]
            if target:
                reverse_edges.setdefault(target, set()).add(importer)

    direct: set[str] = set()
    for module in changed_module_set:
        direct.update(reverse_edges.get(module, set()))
    direct.difference_update(changed_paths)

    transitive: set[str] = set()
    queue = deque(sorted(direct))
    visited_modules = set(changed_module_set)
    while queue and len(transitive) < _MAX_IMPORTERS:
        importer_path = queue.popleft()
        transitive.add(importer_path)
        importer_module = _module_name(importer_path)
        if not importer_module or importer_module in visited_modules:
            continue
        visited_modules.add(importer_module)
        queue.extend(sorted(reverse_edges.get(importer_module, set()) - transitive))

    candidates: set[str] = set()
    for module in changed_modules:
        leaf = module.rsplit(".", 1)[-1]
        candidate = f"tests/test_{leaf}.py"
        if candidate in sources:
            candidates.add(candidate)
        parent = module.rpartition(".")[0]
        if parent:
            package_candidate = f"tests/test_{parent.rsplit('.', 1)[-1]}.py"
            if package_candidate in sources:
                candidates.add(package_candidate)
    for path in sources:
        if path.startswith("tests/") and path.endswith(".py"):
            if path in direct or path in transitive:
                candidates.add(path)

    return WorkspaceChangeImpact(
        changed_paths=changed_paths,
        changed_python_modules=changed_modules,
        direct_importers=tuple(sorted(direct)),
        transitive_importers=tuple(sorted(transitive - direct)),
        test_candidates=tuple(sorted(candidates)),
        configuration_paths=tuple(sorted(path for path in changed_paths if _CONFIG_PATH.search(path))),
        dependency_paths=tuple(sorted(path for path in changed_paths if _DEPENDENCY_PATH.search(path))),
        security_sensitive_paths=tuple(sorted(path for path in changed_paths if _SENSITIVE_PATH.search(path))),
        parse_errors=tuple(sorted(parse_errors)),
        analyzed_python_files=len(imports_by_path),
    )
