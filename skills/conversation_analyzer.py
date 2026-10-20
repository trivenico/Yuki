"""Analyze user-supplied exported conversation files locally within a workspace."""

from pathlib import Path
import json
from skills.base import Skill, SkillResult


class ConversationAnalyzerSkill(Skill):
    name = "conversation_analyzer"
    description = "Read a bounded JSON conversation export from the configured workspace and summarize structural statistics."
    risk = "medium"

    def __init__(self, workspace):
        self.root = Path(workspace).expanduser().resolve()

    def run(self, argument=""):
        rel = argument.strip()
        if not rel or len(rel) > 240:
            return SkillResult(
                False, "Provide a relative path to a JSON export inside the configured workspace."
            )
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            return SkillResult(False, "Path must stay inside the configured workspace.")
        try:
            if (
                target.suffix.lower() != ".json"
                or not target.is_file()
                or target.stat().st_size > 2_000_000
            ):
                return SkillResult(
                    False, "Expected an existing .json file up to 2 MB inside the workspace."
                )
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return SkillResult(False, "Could not safely read a valid JSON export.")
        messages = raw.get("messages") if isinstance(raw, dict) else None
        if not isinstance(messages, list):
            if isinstance(raw, list):
                messages = raw
            else:
                return SkillResult(
                    False, "Unsupported export shape: expected a messages list or top-level list."
                )
        roles = {}
        chars = 0
        for msg in messages[:50000]:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if not isinstance(role, str):
                author = msg.get("author")
                role = author.get("role", "unknown") if isinstance(author, dict) else "unknown"
            roles[role[:40]] = roles.get(role[:40], 0) + 1
            content = msg.get("content", "")
            if isinstance(content, str):
                chars += len(content)
            elif isinstance(content, dict):
                parts = content.get("parts", [])
                if isinstance(parts, list):
                    chars += sum(len(p) for p in parts if isinstance(p, str))
        return SkillResult(
            True,
            "Local structural analysis only. Message text was not uploaded or sent to a model.",
            {
                "file": target.name,
                "messages_examined": min(len(messages), 50000),
                "role_counts": roles,
                "text_characters_estimate": chars,
                "note": "Starter supports common JSON-shaped exports; HTML, ZIP, attachments, semantic summarization, and provider-specific schemas need separate parsers.",
            },
        )
