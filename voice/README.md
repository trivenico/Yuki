# Voice adapters

Voice remains **off by default**. The desktop app now provides an explicit, bounded microphone interaction after the user enables microphone access for the current signed-in session.

- `voice/stt.py`: faster-whisper transcription and finite microphone capture. Captures are bounded to 1–20 seconds at the adapter boundary; the desktop UI currently requests five seconds.
- `voice/session.py`: coordinates an opt-in capture, local transcription, cancellation and temporary-file cleanup. Audio is stored only in a private temporary directory while transcription runs, then removed.
- `voice/tts.py`: optional local Piper or pyttsx3 speech output. It is not yet wired into desktop replies.
- `voice/profile.py`: default Yuki voice presentation profile: feminine, Japanese-anime-inspired English, with bounded rate, pitch and expressiveness preferences. This is preference metadata; it does not itself synthesize or guarantee a particular voice.
- `voice/language.py`: language detection/reply policy and supported voice-style instructions.
- The desktop microphone checkbox is session-scoped. The microphone is not opened until the user presses **Speak**. **Stop microphone** cancels an active capture.
- Recognized text is placed in the message composer for review. It is not sent to Yuki until the user presses **Send**.
- The first transcription may download the configured faster-whisper model. The desktop UI discloses this before enabling voice. Audio is transcribed locally; only the reviewed text is sent through the authenticated conversation.
- No wake-word detector, always-listening service, ambient audio archive, or hands-free duplex conversation is implemented yet. The `openwakeword` dependency is not currently used by a runtime listener.

Microphone permission is independent from camera and screen observation. Sign-out and session cleanup disable voice. If audio capture or the speech model is unavailable, text chat remains available.

- `voice/profiled_tts.py`: reports which requested voice-profile controls the active local synthesizer actually supports; unsupported style, pitch and expressiveness are never claimed as implemented.
