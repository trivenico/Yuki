"""Mood adaptation based on user self-report, never hidden biometric inference."""
from dataclasses import dataclass
from datetime import datetime, timezone

MOODS={"sad","restless","happy","calm","stressed","tired","angry","uncertain","neutral"}
@dataclass(frozen=True)
class MoodResult:
    mood: str
    response_style: str
    source: str = "user_report"
    updated_at: str = ""
class MoodEngine:
    def __init__(self): self._current=None
    def set_mood(self, mood):
        if not isinstance(mood,str) or mood.strip().lower() not in MOODS: raise ValueError("Choose a supported mood or neutral.")
        mood=mood.strip().lower()
        styles={"sad":"gentle and supportive","restless":"calm and grounding","happy":"warm and celebratory","calm":"relaxed","stressed":"clear and reassuring","tired":"brief and low-pressure","angry":"calm and non-confrontational","uncertain":"patient and clarifying","neutral":"natural and conversational"}
        self._current=MoodResult(mood,styles[mood],"user_report",datetime.now(timezone.utc).isoformat())
        return self._current
    def current(self): return self._current
    def clear(self): self._current=None
    def adapt(self, text):
        if not self._current: return text
        return {"mood":self._current.mood,"style":self._current.response_style,"user_message":text}
