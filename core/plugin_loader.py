"""Controlled plugin discovery for trusted skills in the skills package.

Importing Python executes code with Yuki's privileges. This loader only discovers
local modules and validates their class shape; it is not a security sandbox.
"""
from __future__ import annotations
import importlib
import pkgutil
import re

EXCLUDED = {"base", "registry", "discovery", "role_skill", "__init__"}

class PluginLoader:
    def __init__(self, package_name="skills", *, trusted_modules=None, enabled=False):
        self.package_name = package_name
        self.trusted_modules = None if trusted_modules is None else frozenset(trusted_modules)
        self.enabled = bool(enabled)
        self.last_report = {"loaded": [], "skipped": [], "failed": []}

    def discover(self):
        report = {"loaded": [], "skipped": [], "failed": []}
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
        from skills.base import Skill
        for item in sorted(pkgutil.iter_modules(paths), key=lambda x: x.name):
            name = item.name
            if name.startswith("_") or name in EXCLUDED or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                report["skipped"].append({"module": name, "reason": "module name excluded"})
                continue
            if self.trusted_modules is None or ("*" not in self.trusted_modules and name not in self.trusted_modules):
                report["skipped"].append({"module": name, "reason": "module not in explicit trust list"})
                continue
            try:
                module = importlib.import_module(self.package_name + "." + name)
                classes = []
                for candidate in vars(module).values():
                    if (isinstance(candidate, type) and candidate is not Skill
                            and issubclass(candidate, Skill) and candidate.__module__ == module.__name__
                            and not getattr(candidate, "abstract", False)):
                        instance = candidate()
                        if not isinstance(getattr(instance, "name", None), str) or not instance.name:
                            raise ValueError("Plugin skill has no valid name.")
                        if not callable(getattr(instance, "execute", None)):
                            raise TypeError("Plugin skill must implement execute().")
                        classes.append(instance)
                if not classes:
                    report["skipped"].append({"module": name, "reason": "no local Skill subclass found"})
                else:
                    report["loaded"].extend(classes)
            except Exception as exc:
                report["failed"].append({"module": name, "error": type(exc).__name__})
        self.last_report = {
            "loaded": [s.name for s in report["loaded"]],
            "skipped": report["skipped"],
            "failed": report["failed"],
        }
        return report
