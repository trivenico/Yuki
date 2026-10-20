"""Transparent mood heuristic; mood never changes authorization."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Mood:
    mode: str
    prompt: str

_KEYWORDS = {
    "empathetic_caregiver": ("sad", "stressed", "overwhelmed", "anxious"),
    "enthusiastic_friend": ("excited", "amazing", "awesome", "happy"),
    "professional_assistant": ("urgent", "deadline", "work", "report"),
    "casual_companion": ("hey", "lol", "chill", "whatever"),
}
def detect_mood(text: str) -> Mood:
    lowered = text.casefold()
    for mode, words in _KEYWORDS.items():
        if any(word in lowered for word in words):
            return Mood(mode, f"Use a {mode.replace('_', ' ')} tone; remain respectful and clear.")
    return Mood("neutral", "Use a calm, concise, neutral tone.")
