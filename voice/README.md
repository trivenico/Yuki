# Voice adapters

Optional adapter code is present, but voice is **disabled by default**.

- `tts.py`: local pyttsx3 speech output, called only on an explicit CLI request.
- `stt.py`: faster-whisper transcription of a user-selected audio file. It does not capture live microphone audio.
- No wake-word detector, always-listening service, live duplex conversation, or stop-listening control exists yet because no microphone listener is implemented.

Install optional dependencies with `python -m pip install -r requirements-voice.txt`, then explicitly set `YUKI_VOICE_ENABLED=true`. Speech-model downloads can be large. Keep audio files private and do not use untrusted audio paths.


## Local speech-output safety limits

The local Piper playback adapter bounds a single stream to 100 non-empty chunks and
20,000 total text characters (2,000 characters per chunk). Generated WAV output is
limited to 25 MiB, 120 seconds, 8–96 kHz, and one or two channels. Playback has a
125-second watchdog and checks the interruption signal while audio is playing.
The pyttsx3 engine is stopped and released in a `finally` block.

These are resource and lifecycle safeguards, not proof that a selected voice sounds
natural or matches the requested expressive style. Voice selection still requires
auditioning on supported desktop operating systems.
