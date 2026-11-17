import pytest

from voice.profile import DEFAULT_VOICE_PROFILE
from voice.profiled_tts import ProfiledLocalSpeechOutput


class FakeSynthesizer:
    def __init__(self, backend):
        self.backend = backend
        self.rate = 170
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


def test_pyttsx3_applies_only_supported_speaking_rate():
    synth = FakeSynthesizer("pyttsx3")
    output = ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE)
    assert synth.rate == DEFAULT_VOICE_PROFILE.speaking_rate
    assert output.capabilities.supported_controls == frozenset({"speaking_rate"})
    assert output.capabilities.unsupported_controls == frozenset({"pitch", "expressiveness", "style"})
    assert output.capability_report()["requested_style"] == DEFAULT_VOICE_PROFILE.style


def test_piper_does_not_claim_unsupported_rate_or_anime_style():
    synth = FakeSynthesizer("piper")
    output = ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE)
    assert synth.rate == 170
    assert output.capabilities.supported_controls == frozenset()
    assert output.capabilities.unsupported_controls == frozenset(
        {"speaking_rate", "pitch", "expressiveness", "style"}
    )


def test_construction_never_starts_playback():
    synth = FakeSynthesizer("pyttsx3")
    ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE)
    assert synth.spoken == []


def test_explicit_speak_delegates_to_backend():
    synth = FakeSynthesizer("pyttsx3")
    ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE).speak("Hello.")
    assert synth.spoken == ["Hello."]


def test_stream_fallback_skips_empty_chunks_and_enforces_bound():
    synth = FakeSynthesizer("unknown")
    output = ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE)
    assert output.speak_stream(["Hello", "", "Yuki"]) == {
        "chunks_spoken": 2, "interrupted": False
    }
    assert synth.spoken == ["Hello", "Yuki"]
    with pytest.raises(ValueError):
        output.speak_stream(["x" * 2001])


def test_invalid_backend_object_is_rejected():
    with pytest.raises(TypeError):
        ProfiledLocalSpeechOutput(object(), DEFAULT_VOICE_PROFILE)
