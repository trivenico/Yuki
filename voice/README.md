# Voice adapters

Optional adapter code is present, but voice is **disabled by default**.

- `tts.py`: local pyttsx3 speech output, called only on an explicit CLI request.
- `stt.py`: faster-whisper transcription of a user-selected audio file. It does not capture live microphone audio.
- No wake-word detector, always-listening service, live duplex conversation, or stop-listening control exists yet because no microphone listener is implemented.

Install optional dependencies with `python -m pip install -r requirements-voice.txt`, then explicitly set `YUKI_VOICE_ENABLED=true`. Speech-model downloads can be large. Keep audio files private and do not use untrusted audio paths.
