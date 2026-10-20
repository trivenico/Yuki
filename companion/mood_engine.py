"""Mood adaptation based on user self-report, never hidden biometric inference."""

from dataclasses import dataclass
from datetime import datetime, timezone

MOODS = {"sad", "restless", "happy", "calm", "stressed", "tired", "angry", "uncertain", "neutral"}
STYLES = {
    "sad": "gentle and supportive",
    "restless": "calm and grounding",
    "happy": "warm and celebratory",
    "calm": "relaxed",
    "stressed": "clear and reassuring",
    "tired": "brief and low-pressure",
    "angry": "calm and non-confrontational",
    "uncertain": "patient and clarifying",
    "neutral": "natural and conversational",
}


@dataclass(frozen=True)
class MoodResult:
    mood: str
    response_style: str
    source: str
    updated_at: str


class MoodEngine:
    """Adapt tone to an explicit self-report; no face/voice inference or diagnosis."""

    def __init__(self):
        self._current = None

    def set_mood(self, mood):
        if not isinstance(mood, str) or mood.strip().lower() not in MOODS:
            raise ValueError("Choose a supported mood or neutral.")
        mood = mood.strip().lower()
        self._current = MoodResult(
            mood, STYLES[mood], "user_report", datetime.now(timezone.utc).isoformat()
        )
        return self._current

    def current(self):
        return self._current

    def clear(self):
        self._current = None

    def adapt(self, message):
        """Always return the same shape, whether or not a mood is set."""
        if not isinstance(message, str):
            raise TypeError("Message must be text.")
        mood = self._current
        return {
            "message": message,
            "style": mood.response_style if mood else STYLES["neutral"],
            "mood": mood.mood if mood else None,
            "source": mood.source if mood else None,
        }
