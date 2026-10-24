"""Compare Python skill modules with the explicit SkillRegistry wiring.

This audit is static and read-only. A missing direct registry import is a review
candidate; it may be intentionally loaded by another integration or discovery path.
"""
from __future__ import annotations

import ast
from pathlib import Path

INFRA_MODULES = {"__init__", "base", "registry", "discovery"}


def audit_skill_registry(root: str | Path) -> dict:
    base = Path(root).expanduser().resolve()
    skills_dir = base / "skills"
    registry_path = skills_dir / "registry.py"
    if not skills_dir.is_dir() or not registry_path.is_file():
        raise ValueError("Expected skills/ and skills/registry.py under audit root.")

    skill_paths = sorted(
        path for path in skills_dir.glob("*.py")
        if path.is_file() and path.stem not in {"__init__"}
    )
    modules = {path.stem for path in skill_paths}
    candidates = sorted(modules - INFRA_MODULES)
    issues = []
    try:
        if registry_path.stat().st_size > 1024 * 1024:
            raise ValueError("RegistrySourceTooLarge")
        tree = ast.parse(registry_path.read_text(encoding="utf-8"), filename="skills/registry.py")
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        return {
            "skill_module_count": len(candidates),
            "registry_import_count": 0,
            "directly_wired": [],
            "not_directly_wired": candidates,
            "issues": [{"path": "skills/registry.py", "reason": type(exc).__name__}],
            "interpretation": "Registry parse failed; no registration conclusions can be trusted.",
        }

    wired = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("skills."):
                wired.add(module.rsplit(".", 1)[-1])
            elif node.level == 1:
                wired.add(module)
    wired_candidates = sorted(set(candidates) & wired)
    not_wired = sorted(set(candidates) - wired)
    for module in not_wired:
        path = skills_dir / (module + ".py")
        try:
            if path.stat().st_size > 1024 * 1024:
                issues.append({"path": f"skills/{module}.py", "reason": "SourceTooLarge"})
                continue
            ast.parse(path.read_text(encoding="utf-8"), filename=f"skills/{module}.py")
        except (OSError, UnicodeError, SyntaxError) as exc:
            issues.append({"path": f"skills/{module}.py", "reason": type(exc).__name__})

    return {
        "skill_module_count": len(candidates),
        "registry_import_count": len(wired_candidates),
        "directly_wired": wired_candidates,
        "not_directly_wired": not_wired,
        "issues": issues,
        "interpretation": (
            "Direct import is a wiring signal, not proof of successful registration or task-runtime reachability. "
            "Missing direct imports may be intentional; verify dynamic plugin policy and registry construction."
        ),
    }
