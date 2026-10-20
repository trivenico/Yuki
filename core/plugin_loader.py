"""Controlled plugin discovery for explicitly trusted skills.

Importing Python executes code with Yuki's privileges. This loader validates
configuration and module shape, but is NOT a sandbox. Untrusted plugins must
not be enabled until they run in an OS-isolated worker with least privilege.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Iterable

EXCLUDED = {"base", "registry", "discovery", "role_skill", "__init__"}
_MODULE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")


class PluginLoader:
    def __init__(self, package_name="skills", *, trusted_modules=None, enabled=False):
        if not isinstance(package_name, str) or not _MODULE_NAME.fullmatch(package_name.replace(".", "_")):
            raise ValueError("package_name must be a dotted Python package name.")
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean; truthy strings are not accepted.")
        if trusted_modules is None:
            trusted = frozenset()
        else:
            if isinstance(trusted_modules, (str, bytes)) or not isinstance(trusted_modules, Iterable):
                raise TypeError("trusted_modules must be an iterable of exact module names.")
            supplied = tuple(trusted_modules)
            if any(not isinstance(name, str) or not _MODULE_NAME.fullmatch(name) for name in supplied):
                raise ValueError("Each trusted module must be an exact simple module name.")
            if "*" in supplied:
                raise ValueError("Wildcard plugin trust is forbidden; enumerate exact modules.")
            if len(set(supplied)) != len(supplied):
                raise ValueError("Duplicate trusted module names are not allowed.")
            trusted = frozenset(supplied)
        self.package_name = package_name
        self.trusted_modules = trusted
        self.enabled = enabled
        self.last_report = {"loaded": [], "skipped": [], "failed": []}

    def discover(self):
        report = {"loaded": [], "skipped": [], "failed": []}
        if not self.enabled:
            report["skipped"].append({"module": "*", "reason": "plugin discovery disabled"})
            self.last_report = report
            return report
        if not self.trusted_modules:
            report["skipped"].append({"module": "*", "reason": "no exact trusted modules configured"})
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
            if name.startswith("_") or name in EXCLUDED or not _MODULE_NAME.fullmatch(name):
                report["skipped"].append({"module": name, "reason": "module name excluded"})
                continue
            if name not in self.trusted_modules:
                report["skipped"].append({"module": name, "reason": "module not in explicit trust list"})
                continue
            try:
                # SECURITY NOTE: this import executes module top-level code in-process.
                # The trust list is an operator allowlist, not an isolation boundary.
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
