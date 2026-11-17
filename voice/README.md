# Voice adapters

Optional voice components are present, but voice is **disabled by default**.

- `stt.py`: faster-whisper transcription of a user-selected audio file; its bounded recording helper requires explicit invocation.
- `wakeword.py`: optional local openWakeWord adapter with a finite, explicit listen interval. It does not run a background listener or service.
- `session.py`: side-effect-free voice-session state machine. It models opt-in standby, activated capture, processing, conversation, follow-up, mute, and fail-closed transitions. It does not open a microphone.
- `tts.py`: optional local Piper or pyttsx3 speech output, invoked explicitly by the caller.
- No always-listening service, live duplex conversation, or integrated desktop voice workflow exists yet.

Install optional dependencies with `python -m pip install -r requirements-voice.txt`, then explicitly configure voice features. OS microphone permission and explicit user enablement are required for capture. Speech-model downloads can be large. Keep audio files private and do not use untrusted audio paths.

The intended hands-free design is documented in [VOICE_ALWAYS_READY_ARCHITECTURE.md](../docs/VOICE_ALWAYS_READY_ARCHITECTURE.md). Local wake-word standby must not persist or upload idle audio. The future desktop integration must use the authenticated desktop conversation runtime, expose a persistent microphone state and immediate mute, and stop on logout, shutdown, device removal, or permission revocation.
