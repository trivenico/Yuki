"""Opt-in discovery for trusted Python skills placed in the skills package.

Importing a plugin executes its Python module. Only discover code you trust.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from typing import Iterable

from skills.base import Skill

DEFAULT_EXCLUDED = {"base", "registry", "discovery", "__init__"}

def discover_skill_classes(package_name: str = "skills", *, excluded: Iterable[str] = DEFAULT_EXCLUDED):
    """Import package modules and return locally-defined Skill subclasses.

    Modules that fail to import are reported, not allowed to crash boot. This is
    convenience discovery, not a sandbox: plugin Python runs with Yuki's rights.
    """
    package = importlib.import_module(package_name)
    package_path = getattr(package, "__path__", None)
    if package_path is None:
        raise ValueError("Skill discovery target must be a package.")
    excluded_names = set(excluded)
    found = []
    failures = []
    for item in sorted(pkgutil.iter_modules(package_path), key=lambda entry: entry.name):
        module_name = item.name
        if module_name.startswith("_") or module_name in excluded_names or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", module_name):
            continue
        try:
            module = importlib.import_module(f"{package_name}.{module_name}")
        except Exception as exc:
            failures.append({"module": module_name, "error": type(exc).__name__})
            continue
        for value in vars(module).values():
            if (isinstance(value, type) and value is not Skill and issubclass(value, Skill)
                    and value.__module__ == module.__name__ and not getattr(value, "abstract", False)):
                found.append(value)
    return {"classes": found, "failures": failures}
