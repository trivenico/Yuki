"""SKILL.md proposal format and non-executable skill review workflow.

A proposal is declarative metadata plus a prompt/instruction body. It is never imported
as Python or executed as code. Human approval is required before activation.
"""
from __future__ import annotations
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_SKILL_BYTES = 32_000
MAX_SKILL_NAME = 64
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ALLOWED_FIELDS = {"name", "description", "version", "author", "license", "allowed-tools", "metadata"}

@dataclass(frozen=True)
class SkillProposal:
    name: str
    description: str
    version: str
    instructions: str
    source_path: str

def parse_skill_markdown(text: str, *, source_path: str = "<memory>") -> SkillProposal:
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > MAX_SKILL_BYTES:
        raise ValueError("SKILL.md must be non-empty and within 32 KB.")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md requires a YAML-style frontmatter block.")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError("SKILL.md frontmatter is not closed.") from None
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError("Unsupported frontmatter syntax; use simple key: value fields.")
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key not in _ALLOWED_FIELDS or key in metadata:
            raise ValueError("Unknown or duplicate SKILL.md metadata field.")
        metadata[key] = value
    name, description = metadata.get("name", ""), metadata.get("description", "")
    if not _NAME.fullmatch(name):
        raise ValueError("Skill name must be lowercase letters, numbers, and hyphens.")
    if not description or len(description) > 500:
        raise ValueError("Skill description must be 1-500 characters.")
    instructions = "\n".join(lines[end + 1:]).strip()
    if not instructions or len(instructions) > MAX_SKILL_BYTES:
        raise ValueError("Skill instructions must be non-empty and bounded.")
    # Explicitly disallow obvious executable payloads in proposal documents.
    lowered = instructions.lower()
    if any(token in lowered for token in ("<script", "eval(", "exec(", "subprocess.", "os.system(")):
        raise ValueError("Executable code patterns are not accepted in declarative skill proposals.")
    return SkillProposal(name, description, metadata.get("version", "1.0.0"), instructions, source_path)

class SkillProposalStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.proposals = self.root / "proposals"
        self.approved = self.root / "approved"
        self.proposals.mkdir(parents=True, exist_ok=True)
        self.approved.mkdir(parents=True, exist_ok=True)

    def propose(self, text: str) -> SkillProposal:
        proposal = parse_skill_markdown(text)
        target = self.proposals / f"{proposal.name}.SKILL.md"
        if target.exists() or (self.approved / f"{proposal.name}.SKILL.md").exists():
            raise ValueError("A proposal or approved skill with this name already exists.")
        self._atomic_write(target, text)
        return SkillProposal(proposal.name, proposal.description, proposal.version, proposal.instructions, str(target))

    def sandbox_preview(self, name: str) -> dict[str, Any]:
        """Static review only: parse and lint a proposal without executing its instructions."""
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("Invalid skill name.")
        path = (self.proposals / f"{name}.SKILL.md").resolve()
        if path.parent != self.proposals or not path.is_file():
            raise FileNotFoundError("Pending skill proposal was not found.")
        text = path.read_text(encoding="utf-8")
        proposal = parse_skill_markdown(text, source_path=str(path))
        # No Python interpreter, subprocess, filesystem access, network, or model call is
        # launched here. This is intentionally a static preview, not an execution sandbox.
        return {
            "name": proposal.name,
            "description": proposal.description,
            "version": proposal.version,
            "instruction_characters": len(proposal.instructions),
            "checks": {"frontmatter_valid": True, "bounded_size": True,
                       "executable_patterns_rejected": True, "executed": False},
            "status": "static-review-only",
        }

    def list_proposals(self) -> list[dict[str, str]]:
        output = []
        for path in sorted(self.proposals.glob("*.SKILL.md")):
            try:
                proposal = parse_skill_markdown(path.read_text(encoding="utf-8"), source_path=str(path))
                output.append({"name": proposal.name, "description": proposal.description, "version": proposal.version, "path": str(path), "status": "pending"})
            except (OSError, ValueError):
                output.append({"name": path.stem, "description": "Invalid proposal; review required", "version": "", "path": str(path), "status": "invalid"})
        return output

    def approve(self, name: str, *, human_approved: bool) -> Path:
        if human_approved is not True or not isinstance(name, str) or not _NAME.fullmatch(name):
            raise PermissionError("Explicit human approval and a valid skill name are required.")
        source = (self.proposals / f"{name}.SKILL.md").resolve()
        if source.parent != self.proposals or not source.is_file():
            raise FileNotFoundError("Pending skill proposal was not found.")
        # Re-validate at approval time; files may have changed since listing.
        text = source.read_text(encoding="utf-8")
        proposal = parse_skill_markdown(text, source_path=str(source))
        if proposal.name != name:
            raise ValueError("Proposal filename and declared name do not match.")
        target = self.approved / f"{name}.SKILL.md"
        if target.exists():
            raise FileExistsError("Approved skill already exists.")
        self._atomic_write(target, text)
        source.unlink()
        return target

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        fd, temp = tempfile.mkstemp(prefix=".skill-proposal-", dir=str(path.parent))
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
