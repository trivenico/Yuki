# Voice adapters

Voice remains **off by default**. The desktop app now provides an explicit, bounded microphone interaction after the user enables microphone access for the current signed-in session.

- `voice/stt.py`: faster-whisper transcription and finite microphone capture. Captures are bounded to 1–20 seconds at the adapter boundary; the desktop UI currently requests five seconds.
- `voice/session.py`: coordinates an opt-in capture, local transcription, cancellation and temporary-file cleanup. Audio is stored only in a private temporary directory while transcription runs, then removed.
- `voice/tts.py`: optional local Piper or pyttsx3 speech output.
- `voice/profile.py`: default Yuki voice presentation profile: feminine, Japanese-anime-inspired English, with bounded rate, pitch and expressiveness preferences. This is preference metadata; it does not itself synthesize or guarantee a particular voice.
- `voice/language.py`: language detection/reply policy and supported voice-style instructions.
- The desktop microphone checkbox is session-scoped. The microphone is not opened until the user presses **Speak**. **Stop microphone** cancels an active capture.
- Recognized text is placed in the message composer for review. It is not sent to Yuki until the user presses **Send**.
- The first transcription may download the configured faster-whisper model. The desktop UI discloses this before enabling voice. Audio is transcribed locally; only the reviewed text is sent through the authenticated conversation.
- No wake-word detector, always-listening service, ambient audio archive, or hands-free duplex conversation is implemented yet. The `openwakeword` dependency is not currently used by a runtime listener.

Microphone permission is independent from camera and screen observation. Sign-out and session cleanup disable voice. If audio capture or the speech model is unavailable, text chat remains available.

## Local speech-output safety limits

The local Piper playback adapter bounds a single stream to 100 non-empty chunks and
20,000 total text characters (2,000 characters per chunk). Generated WAV output is
limited to 25 MiB, 120 seconds, 8–96 kHz, and one or two channels. Playback has a
125-second watchdog and checks the interruption signal while audio is playing.
The pyttsx3 engine is stopped and released in a `finally` block.

These are resource and lifecycle safeguards, not proof that a selected voice sounds
natural or matches the requested expressive style. Voice selection still requires
auditioning on supported desktop operating systems.

- `voice/profiled_tts.py`: reports which requested voice-profile controls the active local synthesizer actually supports; unsupported style, pitch and expressiveness are never claimed as implemented.

## Explicit desktop spoken replies

- `voice/output_session.py` is the per-session permission gate. It never opens an audio device at construction, requires explicit enablement, bounds reply size and invalidates queued chunks when stopped or revoked.
- `voice/desktop_output.py` validates local backend prerequisites before reporting spoken replies as enabled. Piper requires an existing local model file, the Piper executable, and optional `sounddevice` / `soundfile` packages. pyttsx3 requires its optional package.
- The desktop chat exposes a separate **Enable spoken replies** control and **Stop speaking** action. It does not grant microphone permission. Speech runs on a background worker and permission is revoked on sign-out and shutdown.
- Text chat remains available when local speech dependencies or model files are missing. No wake-word listener, always-on microphone capture, or camera monitoring is enabled by this feature.
