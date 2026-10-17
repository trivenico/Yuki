"""Read deeply only from a host already approved for the web_fetch skill."""
from skills.web_skill import WebSkill, WebSkillError

class DeepReadSkill:
    name="web_deep_read"
    description="Fetch an allow-listed public HTTPS page and return a bounded readable text extract."
    risk="medium"
    requires_approval=True
    def __init__(self,allowed_hosts=()): self.reader=WebSkill(allowed_hosts=allowed_hosts,max_bytes=500000)
    def run(self,argument=""):
        try:
            result=self.reader.execute(argument.strip())
            text=result.get("text","")
            return {"url":result.get("url"),"title":result.get("title"),"text":text[:12000],"characters_returned":min(len(text),12000),"untrusted_content":True}
        except WebSkillError as exc: raise ValueError(str(exc)) from exc
