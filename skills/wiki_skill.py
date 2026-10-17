"""Wikipedia lookup skill using the existing host-allow-listed web reader."""
from skills.base import Skill, SkillResult
from skills.web_skill import WebSkill
class WikiSkill(Skill):
    name="wiki"
    description="Look up Wikipedia pages through the configured allow-listed web reader."
    risk="low"
    requires_approval=False
    def __init__(self, allowed_hosts=()): self.reader=WebSkill(allowed_hosts=allowed_hosts)
    def run(self, argument=""):
        query=argument.strip()
        if not query or len(query)>180: return SkillResult(False,"Provide a short Wikipedia article title.")
        if "wikipedia.org" not in getattr(self.reader,"allowed_hosts",()): return SkillResult(False,"Wikipedia is not enabled in the web host allow-list. Configure it explicitly first.")
        url="https://en.wikipedia.org/wiki/"+query.replace(" ","_")
        try: result=self.reader.run(url)
        except Exception: return SkillResult(False,"Wikipedia lookup failed safely.")
        return SkillResult(bool(getattr(result,"ok",False)),getattr(result,"message","Lookup finished."),getattr(result,"data",None))
