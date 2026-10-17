"""Long-form video planning scaffold; rendering and upload require explicit media providers."""
import json
from skills.base import Skill, SkillResult

class LongVideoGeneratorSkill(Skill):
    name="long_video_generator"
    description="Create a long-form YouTube episode outline and production checklist; no media is rendered."
    risk="medium"
    def run(self,argument=""):
        try: obj=json.loads(argument)
        except (ValueError,TypeError): return SkillResult(False,'Provide JSON {"topic":"...","minutes":12,"audience":"..."}')
        if not isinstance(obj,dict) or not isinstance(obj.get("topic"),str) or not obj["topic"].strip() or len(obj["topic"])>300: return SkillResult(False,"A topic of 1–300 characters is required.")
        minutes=obj.get("minutes",10)
        if not isinstance(minutes,int) or isinstance(minutes,bool) or not 5<=minutes<=60: return SkillResult(False,"Duration must be 5–60 minutes.")
        audience=obj.get("audience","general")
        if not isinstance(audience,str) or len(audience)>120: return SkillResult(False,"Audience must be text up to 120 characters.")
        chapters=max(3,min(8,minutes//3))
        outline=[{"chapter":"Hook and promise","minutes":round(minutes*0.07,2)},{"chapter":"Context and key question","minutes":round(minutes*0.13,2)}]
        remaining=minutes-sum(x["minutes"] for x in outline)-round(minutes*0.08,2)
        each=round(remaining/chapters,2)
        outline += [{"chapter":f"Main chapter {i+1}","minutes":each} for i in range(chapters)]
        outline.append({"chapter":"Summary and next step","minutes":round(minutes*0.08,2)})
        return SkillResult(True,"Outline created; no script model, voice, footage, rendering, or upload was invoked.",{"topic":obj["topic"].strip(),"audience":audience,"target_minutes":minutes,"outline":outline,"production_checks":["Verify claims and citations","Confirm footage/music licenses","Generate captions and transcript","Review audio levels and accessibility","Manually approve before upload"]})
