"""Small, transparent conversational tone helper; not a diagnosis or user profiling."""

from dataclasses import dataclass


_ALLOWED = {"neutral", "warm", "encouraging", "concerned", "celebratory"}


@dataclass
class EmotionState:
    tone: str = "neutral"

    def set_tone(self, tone: str) -> str:
        if not isinstance(tone, str) or tone not in _ALLOWED:
            raise ValueError(f"tone must be one of: {', '.join(sorted(_ALLOWED))}")
        self.tone = tone
        return self.tone

    def guidance(self) -> str:
        return {
            "neutral": "Use a clear, balanced tone.",
            "warm": "Be warm and attentive without pretending to have human feelings.",
            "encouraging": "Be supportive and focus on achievable next steps.",
            "concerned": "Be calm, careful, and practical.",
            "celebratory": "Acknowledge progress without exaggeration.",
        }[self.tone]
