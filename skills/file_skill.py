"""Read-only directory listing constrained to a configured workspace."""

from pathlib import Path
from .base import Skill, SkillResult


class FileSkill(Skill):
    name = "files"
    description = "List entries in the configured workspace; never reads or modifies file contents."

    def __init__(self, workspace: str | Path = "."):
        self.workspace = Path(workspace).expanduser().resolve()

    def run(self, argument: str = "") -> SkillResult:
        relative = argument.strip() or "."
        requested = (self.workspace / relative).resolve()
        try:
            requested.relative_to(self.workspace)
        except ValueError:
            return SkillResult(False, "Path is outside the configured workspace.")
        if not requested.exists():
            return SkillResult(False, "That path does not exist in the workspace.")
        if not requested.is_dir():
            return SkillResult(
                False, "Only directory listing is supported; file contents are never read."
            )
        try:
            entries = sorted(requested.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
        except OSError:
            return SkillResult(False, "Directory could not be listed.")
        names = [
            {"name": item.name, "type": "directory" if item.is_dir() else "file"}
            for item in entries[:100]
            if not item.is_symlink()
        ]
        return SkillResult(
            True, f"Listed {len(names)} entr{'y' if len(names) == 1 else 'ies'}.", names
        )
