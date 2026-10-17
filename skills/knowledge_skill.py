"""Explicitly managed local knowledge graph skill."""
from pathlib import Path
from knowledge.knowledge_graph import KnowledgeGraph
from skills.base import Skill, SkillResult

class KnowledgeSkill(Skill):
    name="knowledge"
    description="Add or search personal knowledge graph entities; local data only."
    risk="medium"
    requires_approval=True
    def __init__(self,workspace): self.graph=KnowledgeGraph(Path(workspace)/"yuki_knowledge_graph.json")
    def run(self,argument=""):
        parts=argument.split(maxsplit=2)
        if not parts: return SkillResult(False,"Use search QUERY or add TYPE | NAME | DESCRIPTION.")
        if parts[0]=="search" and len(parts)>1:
            return SkillResult(True,"Knowledge graph search complete.",self.graph.list_entities(parts[1],limit=50))
        if parts[0]=="add" and len(parts)>1:
            fields=argument[len("add "):].split("|",2)
            if len(fields)<2: return SkillResult(False,"Use add TYPE | NAME | DESCRIPTION")
            kind,name=fields[0].strip(),fields[1].strip(); desc=fields[2].strip() if len(fields)>2 else ""
            item=self.graph.add_entity(name,kind,desc)
            return SkillResult(True,"Knowledge entity saved locally.",item)
        if parts[0]=="list": return SkillResult(True,"Knowledge graph entities.",self.graph.list_entities(limit=100))
        return SkillResult(False,"Use search QUERY, list, or add TYPE | NAME | DESCRIPTION.")
