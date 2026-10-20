"""Stage and validate SKILL.md proposals; never imports or executes generated Python."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

_ID=re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_REQUIRED=("name","description","risk","capabilities")

class SkillProposalStore:
    def __init__(self, root: str | Path):
        self.root=Path(root).expanduser().resolve()
        self.staging=self.root/"staging"; self.active=self.root/"active"
        self.staging.mkdir(parents=True,exist_ok=True); self.active.mkdir(parents=True,exist_ok=True)

    def propose(self, skill_id: str, metadata: dict[str,Any], instructions: str) -> dict[str,str]:
        if not isinstance(skill_id,str) or not _ID.fullmatch(skill_id): raise ValueError("Invalid skill id")
        if not isinstance(metadata,dict) or any(k not in metadata for k in _REQUIRED): raise ValueError("Required metadata missing")
        if metadata["risk"] not in {"low","medium","high","critical"}: raise ValueError("Invalid risk")
        caps=metadata["capabilities"]
        if not isinstance(caps,list) or any(not isinstance(c,str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}",c) for c in caps): raise ValueError("Invalid capabilities")
        if not isinstance(metadata["name"],str) or not 1<=len(metadata["name"])<=100: raise ValueError("Invalid name")
        if not isinstance(metadata["description"],str) or not 1<=len(metadata["description"])<=1000: raise ValueError("Invalid description")
        if not isinstance(instructions,str) or not instructions.strip() or len(instructions)>20000: raise ValueError("Instructions must be bounded text")
        body="---\n"+json.dumps(metadata,indent=2,ensure_ascii=False)+"\n---\n\n"+instructions.strip()+"\n"
        digest=hashlib.sha256(body.encode()).hexdigest()
        path=self.staging/(skill_id+".SKILL.md")
        if path.exists() or (self.active/(skill_id+".SKILL.md")).exists(): raise ValueError("Skill id already exists")
        self._atomic_write(path,body)
        return {"skill_id":skill_id,"status":"pending_review","sha256":digest,"path":str(path)}

    def review(self, skill_id: str, decision: str, *, expected_sha256: str) -> dict[str,str]:
        if decision not in {"accept","reject"}: raise ValueError("Decision must be accept or reject")
        source=self.staging/(skill_id+".SKILL.md")
        if not source.is_file(): raise FileNotFoundError("Pending skill proposal not found")
        body=source.read_text(encoding="utf-8")
        digest=hashlib.sha256(body.encode()).hexdigest()
        if not hmac_compare(digest,expected_sha256): raise ValueError("Proposal changed since review")
        if decision=="reject":
            source.unlink()
            return {"skill_id":skill_id,"status":"rejected","sha256":digest}
        metadata,instructions=self._parse(body)
        if metadata.get("risk")!="low":
            raise PermissionError("Only low-risk declarative skills can be activated automatically; higher-risk skills require separate security review")
        destination=self.active/(skill_id+".SKILL.md")
        if destination.exists(): raise ValueError("Active skill already exists")
        os.replace(source,destination)
        return {"skill_id":skill_id,"status":"active_declarative","sha256":digest,"path":str(destination)}

    @staticmethod
    def _parse(body: str):
        if not body.startswith("---\n"): raise ValueError("Invalid SKILL.md frontmatter")
        end=body.find("\n---\n",4)
        if end<0: raise ValueError("Missing SKILL.md frontmatter terminator")
        try: metadata=json.loads(body[4:end])
        except json.JSONDecodeError as exc: raise ValueError("Frontmatter must be JSON for unambiguous parsing") from exc
        if not isinstance(metadata,dict) or any(k not in metadata for k in _REQUIRED): raise ValueError("Invalid frontmatter")
        if not body[end+5:].strip(): raise ValueError("Skill instructions are empty")
        return metadata,body[end+5:]

    @staticmethod
    def _atomic_write(path: Path, body: str):
        fd,tmp=tempfile.mkstemp(prefix=".skill-",dir=path.parent)
        try:
            try: os.fchmod(fd,0o600)
            except OSError: pass
            with os.fdopen(fd,"w",encoding="utf-8") as f: f.write(body); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

def hmac_compare(left: str,right: str) -> bool:
    import hmac
    return hmac.compare_digest(left,right)
