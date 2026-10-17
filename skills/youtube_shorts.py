"""YouTube Shorts production brief; rendering requires explicit TTS/media backends."""
import json
from skills.base import Skill, SkillResult

class YouTubeShortsSkill(Skill):
    name="youtube_shorts"
    description="Build a bounded vertical-video script and shot plan; does not generate audio or render video."
    risk="medium"
    def run(self,argument=""):
        try: obj=json.loads(argument)
        except (ValueError,TypeError): return SkillResult(False,'Provide JSON {"topic":"...","audience":"...","seconds":30}.')
        if not isinstance(obj,dict) or not isinstance(obj.get("topic"),str) or not obj["topic"].strip() or len(obj["topic"])>300: return SkillResult(False,"A topic of 1–300 characters is required.")
        seconds=obj.get("seconds",30)
        if not isinstance(seconds,int) or isinstance(seconds,bool) or not 15<=seconds<=60: return SkillResult(False,"Shorts duration must be 15–60 seconds.")
        topic=obj["topic"].strip(); audience=obj.get("audience","general")
        if not isinstance(audience,str) or len(audience)>120: return SkillResult(False,"Audience must be text up to 120 characters.")
        return SkillResult(True,"Production brief created; no TTS, footage, upload, or rendering occurred.",{"format":"9:16 vertical","target_seconds":seconds,"topic":topic,"audience":audience,"beats":[{"time":"0–3s","purpose":"Hook: a concrete question or surprising, supportable fact"},{"time":"3–"+str(max(4,seconds-8))+"s","purpose":"Deliver 2–3 concise points with visual cut suggestions"},{"time":str(max(4,seconds-7))+"–"+str(seconds-2)+"s","purpose":"Payoff or practical takeaway"},{"time":str(seconds-2)+"–"+str(seconds)+"s","purpose":"Clear, non-misleading call to action"}],"checks":["Verify factual claims and rights to media/music.","Add readable captions and check mobile safe areas.","Do not imply results or endorsements that cannot be substantiated."]})
