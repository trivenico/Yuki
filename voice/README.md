# Voice adapters

Optional adapter code is present, but voice is **disabled by default**.

- `tts.py`: local pyttsx3 speech output, called only on an explicit CLI request; Piper is an optional local synthesis backend.
- `profile.py`: default Yuki voice presentation profile: feminine, Japanese-anime-inspired English, with bounded rate, pitch and expressiveness preferences. This is preference metadata; it does not itself synthesize or guarantee a particular voice.
- `language.py`: language detection/reply policy and supported voice-style instructions.
- `stt.py`: faster-whisper transcription of user-selected audio and bounded explicit microphone capture. It does not implement an always-on listener.
- No always-listening service or full hands-free duplex conversation is enabled by this profile.

The configured TTS engine and exact voice model determine the audible timbre. A future voice-model selection must verify the model, voice, training-data and phonemizer licenses independently; runtime-library licensing alone is insufficient. The profile is designed to avoid imitation of a specific actor or copyrighted character.

Install optional dependencies with `python -m pip install -r requirements-voice.txt`, then explicitly enable voice. Speech-model downloads can be large. Keep audio files private and do not use untrusted audio paths.
