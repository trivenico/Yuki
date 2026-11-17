"""Language and spoken-reply policy helpers for future conversational providers.

These helpers express preferences; they do not provide translation, language models,
or voice models by themselves.
"""
from __future__ import annotations

import re


SUPPORTED_BEHAVIOR = {"auto_detect"}
SUPPORTED_VOICE_STYLES = {
    "natural_realistic_female",
    "japanese_anime_female_english",
}
SUPPORTED_SPOKEN_POLICIES = {"every_reply_when_voice_mode_active"}


def normalize_language_code(value):
    """Return a conservative BCP-47-like language tag, or None when unknown."""
    if not isinstance(value, str):
        return None
    value = value.strip().replace("_", "-")
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value):
        return None
    parts = value.split("-")
    return "-".join([parts[0].lower(), *[p.title() if len(p) == 4 and p.isalpha()
                                         else p.upper() if len(p) == 2 and p.isalpha()
                                         else p for p in parts[1:]]])


def same_language_reply_instruction(detected_language):
    """Build a provider instruction only when STT returned a valid language tag."""
    language = normalize_language_code(detected_language)
    if language is None:
        return None
    return (
        f"The user's latest speech was detected as language tag '{language}'. "
        "Reply in that language unless the user explicitly requests another language. "
        "Do not claim perfect language detection; ask a concise clarification if needed."
    )


def voice_style_instruction(style):
    """Return bounded conversational direction for a supported voice style."""
    if style == "japanese_anime_female_english":
        return (
            "Speak in English using a feminine, Japanese-anime-inspired voice-acting style. "
            "Use clear, natural English pronunciation, warm and friendly delivery, gentle "
            "expressiveness, and subtle playful energy when appropriate. Keep the voice "
            "pleasant and conversational rather than exaggerated, childish, breathy, or robotic. "
            "Maintain a consistent adult character presentation. Do not imitate a specific "
            "real person, voice actor, or copyrighted character."
        )
    if style == "natural_realistic_female":
        return (
            "Use a natural, clear, warm feminine speaking style with conversational pacing "
            "and restrained expressiveness."
        )
    raise ValueError("Unsupported voice style.")


def should_speak_reply(*, voice_enabled, voice_mode_active, reply_text):
    """Gate speech to explicit voice mode and non-empty assistant replies."""
    return bool(
        voice_enabled is True
        and voice_mode_active is True
        and isinstance(reply_text, str)
        and reply_text.strip()
    )
