"""Non-executing skill file discovery for human-reviewed skill activation.

Discovery deliberately reads bytes and hashes candidate files only. It never imports
Python modules: importing a skill can execute arbitrary code in the host process.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any


_MODULE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}\.py\Z")
_MAX_SKILL_BYTES = 512 * 1024


class SkillDiscovery:
    """Find changed skill candidates without importing or activating them."""

    def __init__(self, skills_dir: str | os.PathLike[str], *, max_file_bytes: int = _MAX_SKILL_BYTES):
        self.skills_dir = Path(skills_dir)
        if type(max_file_bytes) is not int or not 1 <= max_file_bytes <= _MAX_SKILL_BYTES:
            raise ValueError("max_file_bytes must be between 1 and 524288")
        self.max_file_bytes = max_file_bytes
        self._known_hashes: dict[str, str] = {}

    def scan(self) -> dict[str, list[dict[str, Any]]]:
        report: dict[str, list[dict[str, Any]]] = {
            "pending_review": [],
            "unchanged": [],
            "rejected": [],
        }
        try:
            root = self.skills_dir.resolve(strict=True)
        except OSError:
            report["rejected"].append({"file": str(self.skills_dir), "reason": "skills directory unavailable"})
            return report
        if not root.is_dir():
            report["rejected"].append({"file": str(root), "reason": "skills path is not a directory"})
            return report

        try:
            entries = sorted(root.iterdir(), key=lambda path: path.name)
        except OSError:
            report["rejected"].append({"file": str(root), "reason": "skills directory unreadable"})
            return report

        present: set[str] = set()
        for path in entries:
            name = path.name
            if name.startswith("_") or name in {"base.py", "registry.py", "discovery.py"}:
                continue
            if path.suffix != ".py":
                continue
            if not _MODULE_NAME.fullmatch(name):
                report["rejected"].append({"file": name, "reason": "invalid skill filename"})
                continue
            present.add(name)
            try:
                if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
                    report["rejected"].append({"file": name, "reason": "symlink or path escape"})
                    continue
                if not path.is_file():
                    report["rejected"].append({"file": name, "reason": "not a regular file"})
                    continue
                if path.stat().st_size > self.max_file_bytes:
                    report["rejected"].append({"file": name, "reason": "file exceeds size limit"})
                    continue
                with path.open("rb") as source:
                    data = source.read(self.max_file_bytes + 1)
                if len(data) > self.max_file_bytes:
                    report["rejected"].append({"file": name, "reason": "file exceeds size limit"})
                    continue
            except OSError:
                report["rejected"].append({"file": name, "reason": "file unreadable"})
                continue

            digest = hashlib.sha256(data).hexdigest()
            record = {"file": name, "sha256": digest, "status": "pending_review"}
            if self._known_hashes.get(name) == digest:
                report["unchanged"].append({**record, "status": "unchanged"})
            else:
                report["pending_review"].append(record)
                self._known_hashes[name] = digest

        for name in set(self._known_hashes) - present:
            self._known_hashes.pop(name, None)
        return report
