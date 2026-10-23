"""Controlled, trust-gated discovery for Python skills.

Candidate discovery uses AST parsing first so untrusted candidate modules are not
imported just to decide whether they look like skills. Trusted modules are still
executed when imported; this loader is not a sandbox.
"""
from __future__ import annotations

import ast
import importlib
import pkgutil
import re
from pathlib import Path

EXCLUDED = {"base", "registry", "discovery", "skill_catalog", "role_skill", "__init__"}
_MODULE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_MAX_SOURCE_BYTES = 512 * 1024


def _static_candidates(paths):
    """Return modules containing a syntactically recognizable local Skill subclass."""
    candidates = set()
    failures = []
    for package_path in paths:
        directory = Path(package_path)
        for path in sorted(directory.glob("*.py"), key=lambda item: item.name):
            name = path.stem
            if name.startswith("_") or name in EXCLUDED or not _MODULE_RE.fullmatch(name):
                continue
            try:
                source = path.read_bytes()
                if len(source) > _MAX_SOURCE_BYTES:
                    failures.append({"module": name, "error": "SourceTooLarge"})
                    continue
                tree = ast.parse(source.decode("utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                failures.append({"module": name, "error": type(exc).__name__})
                continue
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = [base.id if isinstance(base, ast.Name) else base.attr
                         for base in node.bases if isinstance(base, (ast.Name, ast.Attribute))]
                if any(base == "Skill" or base.endswith("Skill") for base in bases):
                    if any(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                           and child.name == "execute" for child in node.body):
                        candidates.add(name)
                        break
    return candidates, failures


class PluginLoader:
    def __init__(self, package_name="skills", *, trusted_modules=None, enabled=False):
        self.package_name = package_name
        self.trusted_modules = None if trusted_modules is None else frozenset(trusted_modules)
        self.enabled = bool(enabled)
        self.last_report = {"loaded": [], "skipped": [], "failed": [], "candidates": []}

    def discover(self):
        report = {"loaded": [], "skipped": [], "failed": [], "candidates": []}
        if not self.enabled:
            report["skipped"].append({"module": "*", "reason": "plugin discovery disabled"})
            self.last_report = report
            return report
        try:
            package = importlib.import_module(self.package_name)
            paths = getattr(package, "__path__", None)
            if paths is None:
                raise ValueError("Target is not a Python package.")
        except Exception as exc:
            report["failed"].append({"module": self.package_name, "error": type(exc).__name__})
            self.last_report = report
            return report

        candidates, parse_failures = _static_candidates(paths)
        report["candidates"] = sorted(candidates)
        report["failed"].extend(parse_failures)
        try:
            from skills.base import Skill
        except Exception as exc:
            report["failed"].append({"module": "skills.base", "error": type(exc).__name__})
            self.last_report = report
            return report

        for item in sorted(pkgutil.iter_modules(paths), key=lambda x: x.name):
            name = item.name
            if name.startswith("_") or name in EXCLUDED or not _MODULE_RE.fullmatch(name):
                report["skipped"].append({"module": name, "reason": "module name excluded"})
                continue
            if name not in candidates:
                report["skipped"].append({"module": name, "reason": "static scan found no Skill subclass with execute"})
                continue
            if self.trusted_modules is None or ("*" not in self.trusted_modules and name not in self.trusted_modules):
                report["skipped"].append({"module": name, "reason": "candidate is not in explicit trust list"})
                continue
            try:
                module = importlib.import_module(self.package_name + "." + name)
                instances = []
                for candidate in vars(module).values():
                    if (isinstance(candidate, type) and candidate is not Skill
                            and issubclass(candidate, Skill) and candidate.__module__ == module.__name__
                            and not getattr(candidate, "abstract", False)):
                        instance = candidate()
                        if not isinstance(getattr(instance, "name", None), str) or not instance.name:
                            raise ValueError("Plugin skill has no valid name.")
                        if not callable(getattr(instance, "execute", None)):
                            raise TypeError("Plugin skill must implement execute().")
                        instances.append(instance)
                if not instances:
                    report["skipped"].append({"module": name, "reason": "no constructible local Skill subclass found"})
                else:
                    report["loaded"].extend(instances)
            except Exception as exc:
                report["failed"].append({"module": name, "error": type(exc).__name__})

        self.last_report = {
            "loaded": [skill.name for skill in report["loaded"]],
            "skipped": report["skipped"],
            "failed": report["failed"],
            "candidates": report["candidates"],
        }
        return report
