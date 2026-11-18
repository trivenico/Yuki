"""Strict, dependency-light validation for voice-cloning reference audio.

Only bounded PCM WAV input is accepted here. Decoding/model inference belongs
in a separate isolated worker; this module never opens devices or writes files.
"""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass


MAX_SAMPLE_BYTES = 10 * 1024 * 1024
MIN_DURATION_SECONDS = 1.0
MAX_DURATION_SECONDS = 30.0
MIN_SAMPLE_RATE = 16_000
MAX_SAMPLE_RATE = 48_000
MAX_CHANNELS = 2
REQUIRED_SAMPLE_WIDTH = 2


class VoiceSampleValidationError(ValueError):
    """Raised when a voice sample is not safe supported PCM WAV input."""


@dataclass(frozen=True)
class VoiceSampleMetadata:
    container: str
    codec: str
    sample_rate: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    duration_seconds: float
    size_bytes: int


def validate_voice_sample(data: bytes, *, source_owner_consent: bool, max_bytes: int = MAX_SAMPLE_BYTES) -> VoiceSampleMetadata:
    """Validate a consented WAV byte buffer before it reaches a clone provider."""
    if source_owner_consent is not True:
        raise VoiceSampleValidationError("Explicit source-owner consent is required.")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= MAX_SAMPLE_BYTES:
        raise ValueError("max_bytes must be between 1 byte and 10 MiB.")
    if not isinstance(data, bytes) or not data:
        raise VoiceSampleValidationError("Voice sample must be non-empty bytes.")
    if len(data) > max_bytes:
        raise VoiceSampleValidationError("Voice sample exceeds the configured byte limit.")
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise VoiceSampleValidationError("Only standard RIFF/WAVE PCM audio is supported.")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frame_count = audio.getnframes()
            compression = audio.getcomptype()
            compression_name = audio.getcompname()
            duration = frame_count / sample_rate if sample_rate else 0.0
            expected = frame_count * channels * sample_width
            if expected > len(data):
                raise VoiceSampleValidationError("Voice sample declares more audio data than the file contains.")
            frames = audio.readframes(frame_count)
            if len(frames) != expected:
                raise VoiceSampleValidationError("Voice sample frame data is truncated.")
    except VoiceSampleValidationError:
        raise
    except (wave.Error, EOFError, OSError, ZeroDivisionError) as exc:
        raise VoiceSampleValidationError("Voice sample is malformed or unsupported.") from exc
    if compression != "NONE" or compression_name.lower() != "not compressed":
        raise VoiceSampleValidationError("Compressed WAV audio is not supported.")
    if channels < 1 or channels > MAX_CHANNELS:
        raise VoiceSampleValidationError("Voice sample must be mono or stereo.")
    if sample_width != REQUIRED_SAMPLE_WIDTH:
        raise VoiceSampleValidationError("Voice sample must use 16-bit PCM audio.")
    if not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
        raise VoiceSampleValidationError("Voice sample sample-rate must be between 16 kHz and 48 kHz.")
    if not MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS:
        raise VoiceSampleValidationError("Voice sample duration must be between 1 and 30 seconds.")
    return VoiceSampleMetadata("wav", "pcm_s16le", sample_rate, channels, sample_width, frame_count, duration, len(data))
