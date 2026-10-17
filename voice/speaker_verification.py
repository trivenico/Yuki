"""Optional speaker-verification policy interface; no identity claim without a model.

This is a fail-closed integration boundary. Real speaker verification requires a
validated model, enrolled consented samples, and spoof/replay testing.
"""
from __future__ import annotations


class SpeakerVerificationError(RuntimeError):
    pass


class SpeakerVerifier:
    def __init__(self, *, enabled: bool = False):
        self.enabled = enabled

    def verify(self, audio_path: str) -> dict:
        if not self.enabled:
            raise SpeakerVerificationError("Speaker verification is disabled.")
        raise SpeakerVerificationError(
            "No verified speaker model is configured. Voice alone must not authorize sensitive actions."
        )
