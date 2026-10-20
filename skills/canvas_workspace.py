"""Local markdown workspace helper, not a real-time collaborative editor."""

from pathlib import Path
from skills.base import Skill, SkillResult
import re


class CanvasWorkspaceSkill(Skill):
    name = "canvas_workspace"
    description = "Create and list bounded local Markdown workspace documents; no collaboration service is connected."
    risk = "medium"

    def __init__(self, workspace):
        self.root = Path(workspace).resolve() / "canvas"

    def run(self, argument=""):
        if argument.strip() == "list":
            self.root.mkdir(parents=True, exist_ok=True)
            return SkillResult(
                True,
                "Local workspace documents.",
                sorted(p.name for p in self.root.glob("*.md"))[:100],
            )
        if not argument.startswith("create "):
            return SkillResult(False, "Use list or create TITLE|CONTENT.")
        payload = argument[7:]
        if "|" not in payload:
            return SkillResult(False, "Use create TITLE|CONTENT.")
        title, content = payload.split("|", 1)
        title = title.strip()
        if not title or len(title) > 80 or len(content) > 12000:
            return SkillResult(False, "Title/content exceeds limits.")
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-")[:60]
        if not safe:
            return SkillResult(False, "Title must contain letters or numbers.")
        self.root.mkdir(parents=True, exist_ok=True)
        target = (self.root / (safe + ".md")).resolve()
        if target.parent != self.root:
            return SkillResult(False, "Unsafe path.")
        if target.exists():
            return SkillResult(
                False, "A document with that title already exists; refusing to overwrite."
            )
        try:
            target.write_text("# " + title + "\n\n" + content, encoding="utf-8")
        except OSError:
            return SkillResult(False, "Could not write document.")
        return SkillResult(
            True,
            "Created a local Markdown document. Real-time collaboration is not implemented.",
            {"file": target.name},
        )
