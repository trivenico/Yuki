# Voice profile to local TTS capability contract

`profiled_tts.py` bridges the requested `VoiceProfile` to an existing local
speech synthesizer without pretending that metadata changes the generated
voice. It applies speaking rate only to the current `pyttsx3` adapter, whose
rate property is supported. The current Piper adapter does not receive a rate
parameter, and neither adapter exposes reliable pitch, expressiveness, or
Japanese-anime-inspired voice-acting controls.

The adapter reports requested style separately from controls the backend can
actually honor. A requested feminine anime-inspired English style is therefore
not a promise about audible timbre. The final voice requires model/backend
selection, licensed voice assets, and listening evaluation on supported
hardware.

Constructing the adapter never starts playback. Speech occurs only when an
authorized voice-session controller explicitly calls `speak` or
`speak_stream`. This module does not capture microphone audio, start a
wake-word listener, or enable always-on voice.
