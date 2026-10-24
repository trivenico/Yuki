"""Static skill catalog and policy-gated auto-registration helpers.

Discovery parses Python AST and never imports a candidate module. Registration is
performed only for explicitly trusted module names and happens through the existing
SkillRegistry after normal imports/instantiation. This is not a sandbox.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

_MODULE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_EXCLUDED = {"__init__", "base", "registry", "discovery", "skill_catalog"}
MAX_SOURCE_BYTES = 512 * 1024


@dataclass(frozen=True)
class SkillCandidate:
    module: str
    path: str
    class_names: tuple[str, ...]
    declared_names: tuple[str, ...]
    has_execute_method: bool


@dataclass(frozen=True)
class SkillCatalogReport:
    candidates: tuple[SkillCandidate, ...]
    skipped: tuple[dict, ...]
    failed: tuple[dict, ...]


def discover_skill_candidates(package_dir: str | Path) -> SkillCatalogReport:
    """Statically inspect direct Python files without importing/executing them."""
    directory = Path(package_dir).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError("package_dir must be an existing directory")
    candidates = []
    skipped = []
    failed = []
    for path in sorted(directory.glob("*.py"), key=lambda p: p.name):
        module = path.stem
        if module.startswith("_") or module in _EXCLUDED or not _MODULE_RE.fullmatch(module):
            skipped.append({"module": module, "reason": "excluded module name"})
            continue
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_SOURCE_BYTES:
                skipped.append({"module": module, "reason": "source exceeds size limit"})
                continue
            tree = ast.parse(raw.decode("utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            failed.append({"module": module, "error": type(exc).__name__})
            continue
        classes = []
        names = []
        has_execute = False
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = {base.id for base in node.bases if isinstance(base, ast.Name)}
                bases.update(base.attr for base in node.bases if isinstance(base, ast.Attribute))
                # Static shape hint only; imports/aliases/dynamic inheritance may be missed.
                if "Skill" in bases or any("Skill" in base for base in bases):
                    classes.append(node.name)
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "execute":
                            has_execute = True
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id in {"SKILL_NAME", "name"} for t in targets):
                    names.append("declared-name")
        if classes:
            candidates.append(SkillCandidate(module, str(path), tuple(sorted(set(classes))),
                                             tuple(names), has_execute))
        else:
            skipped.append({"module": module, "reason": "no statically identifiable Skill subclass"})
    return SkillCatalogReport(tuple(candidates), tuple(skipped), tuple(failed))


def register_trusted_modules(registry, *, package_dir: str | Path,
                             trusted_modules: Iterable[str], package_name: str = "skills") -> dict:
    """Load only operator-trusted discovered modules, then validate via registry.register.

    This executes trusted Python imports with the current process privileges. Never
    pass an untrusted or user-uploaded module into trusted_modules.
    """
    import importlib

    trusted = frozenset(trusted_modules)
    if any(not isinstance(name, str) or not _MODULE_RE.fullmatch(name) for name in trusted):
        raise ValueError("trusted_modules must contain simple module names")
    report = discover_skill_candidates(package_dir)
    by_module = {candidate.module: candidate for candidate in report.candidates}
    loaded, skipped, failed = [], list(report.skipped), list(report.failed)
    for module in sorted(trusted):
        candidate = by_module.get(module)
        if candidate is None:
            skipped.append({"module": module, "reason": "not a discovered skill candidate"})
            continue
        try:
            imported = importlib.import_module(f"{package_name}.{module}")
            from skills.base import Skill
            instances = []
            for value in vars(imported).values():
                if (isinstance(value, type) and value is not Skill and issubclass(value, Skill)
                        and value.__module__ == imported.__name__ and not getattr(value, "abstract", False)):
                    # Auto-construction is allowed only for zero-argument constructors.
                    instance = value()
                    registry.register(instance)
                    instances.append(instance.name)
            if not instances:
                skipped.append({"module": module, "reason": "no constructible local Skill subclass"})
            else:
                loaded.extend({"module": module, "skill": name} for name in instances)
        except Exception as exc:
            failed.append({"module": module, "error": type(exc).__name__})
    return {"loaded": loaded, "skipped": skipped, "failed": failed}
