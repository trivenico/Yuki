"""Local searchable chat snippets; user supplies content explicitly."""
from pathlib import Path
from skills.base import Skill, SkillResult
from skills.safe_local import load_json, save_json, parse_object

class ChatOrganizerSkill(Skill):
    name = "chat_organizer"
    description = "Index, search, and clear user-provided chat notes in a local JSON file."
    risk = "medium"
    def __init__(self, workspace): self.path = Path(workspace).resolve() / "chat_index.json"
    def run(self, argument=""):
        rows = load_json(self.path, [])
        if argument.startswith("search "):
            q = argument[7:].strip().casefold()
            if not q: return SkillResult(False, "Provide search text.")
            hits = [{"id": r["id"], "title": r["title"], "text": r["text"][:240]} for r in rows if q in (r["title"] + " " + r["text"]).casefold()]
            return SkillResult(True, f"Found {len(hits)} matching local entries.", hits[:20])
        if argument.strip() == "list": return SkillResult(True, f"{len(rows)} local chat entries.", [{"id":r["id"],"title":r["title"]} for r in rows[-100:]])
        if argument.strip() == "clear":
            if not save_json(self.path, []): return SkillResult(False, "Could not clear chat index.")
            return SkillResult(True, "Local chat index cleared.")
        obj = parse_object(argument)
        if not obj or not isinstance(obj.get("text"), str) or not isinstance(obj.get("title", ""), str):
            return SkillResult(False, 'Use list, search WORDS, clear, or JSON {"title":"...","text":"..."} to add a user-provided note.')
        text = obj["text"].strip(); title = obj.get("title", "Untitled").strip()[:120]
        if not text or len(text) > 4000: return SkillResult(False, "Text must be 1–4000 characters.")
        import uuid
        rows.append({"id":uuid.uuid4().hex[:12],"title":title or "Untitled","text":text})
        if not save_json(self.path, rows[-500:]): return SkillResult(False, "Could not save local chat entry.")
        return SkillResult(True, "Added a local searchable entry; this does not import chat history automatically.", {"count":min(len(rows),500)})
