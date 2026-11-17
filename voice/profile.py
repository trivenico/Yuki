"""Default voice identity and user-adjustable speech presentation for Yuki.

This profile is preference metadata, not a synthesizer or a bundled voice model.
"""
from __future__ import annotations

from dataclasses import dataclass

from voice.language import SUPPORTED_VOICE_STYLES, voice_style_instruction


@dataclass(frozen=True)
class VoiceProfile:
    profile_id: str = "yuki-feminine-anime-english"
    language: str = "en"
    style: str = "japanese_anime_female_english"
    speaking_rate: int = 170
    expressiveness: float = 0.65
    pitch: float = 0.55

    def __post_init__(self):
        if self.style not in SUPPORTED_VOICE_STYLES:
            raise ValueError("Unsupported voice style.")
        if self.language != "en":
            raise ValueError("The default Yuki voice profile must use English.")
        if isinstance(self.speaking_rate, bool) or not isinstance(self.speaking_rate, int) or not 100 <= self.speaking_rate <= 240:
            raise ValueError("speaking_rate must be between 100 and 240.")
        for name, value in (("expressiveness", self.expressiveness), ("pitch", self.pitch)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1.")

    def instruction(self):
        return voice_style_instruction(self.style)


DEFAULT_VOICE_PROFILE = VoiceProfile()
