# Voice adapters

Voice remains **off by default**. The desktop app now provides an explicit, bounded microphone interaction after the user enables microphone access for the current signed-in session.

- `voice/stt.py`: faster-whisper transcription and finite microphone capture. Captures are bounded to 1–20 seconds at the adapter boundary; the desktop UI currently requests five seconds.
- `voice/session.py`: coordinates an opt-in capture, local transcription, cancellation and temporary-file cleanup. Audio is stored only in a private temporary directory while transcription runs, then removed.
- `voice/tts.py`: optional local Piper or pyttsx3 speech output. Playback runs only from the desktop background worker.
- `voice/output_session.py`: requires explicit spoken-reply opt-in, bounds reply size, and supports Stop/disable/close with per-chunk permission checks.
- `voice/desktop_output.py`: constructs the configured local TTS backend lazily and keeps it disabled until the user confirms the spoken-reply prompt.
- Desktop chat has a separate **Enable spoken replies** control. It does not grant microphone access; spoken replies are off by default and stop/disable on sign-out or application shutdown. TTS backend errors are reported without disabling text chat.
- The desktop microphone checkbox is session-scoped. The microphone is not opened until the user presses **Speak**. **Stop microphone** cancels an active capture.
- Recognized text is placed in the message composer for review. It is not sent to Yuki until the user presses **Send**.
- The first transcription may download the configured faster-whisper model. The desktop UI discloses this before enabling voice. Audio is transcribed locally; only the reviewed text is sent through the authenticated conversation.
- No wake-word detector, always-listening service, ambient audio archive, or hands-free duplex conversation is implemented yet. The `openwakeword` dependency is not currently used by a runtime listener.

Microphone permission is independent from camera and screen observation. Sign-out and session cleanup disable voice. If audio capture or the speech model is unavailable, text chat remains available.

- `voice/output_session.py`: explicit opt-in speech-output gate. It does not create audio devices or speak at construction, requires an active voice-output mode, bounds replies, supports stop/disable/close, and guards chunks against permission-revocation races. Call `speak_reply()` from a background worker, not the Qt UI thread.
