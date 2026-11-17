# Local TTS engine evaluation: Kokoro via sherpa-onnx

Status: **candidate for a controlled desktop audition; not approved for bundling yet**

## Recommendation

Evaluate the official sherpa-onnx `kokoro-en-v0_19` package as the first higher-quality offline English TTS candidate. Keep Piper and pyttsx3 as compatibility fallbacks until Kokoro passes packaging, licensing, performance, interruption, and listening acceptance on Windows, macOS, and Linux.

Do not bundle or redistribute model/runtime assets until the exact release artifacts and every included dependency have a documented license and provenance record.

## Candidate voice shortlist

The English v0.19 pack documents 11 speaker IDs. Female English candidates:

| Speaker ID | Preset | Accent |
|---:|---|---|
| 1 | `af_bella` | American English |
| 2 | `af_nicole` | American English |
| 3 | `af_sarah` | American English |
| 4 | `af_sky` | American English |
| 7 | `bf_emma` | British English |
| 8 | `bf_isabella` | British English |

These are female-labelled English presets, not verified anime-style voices. Final selection requires listening tests using identical prompts and playback conditions. Do not infer personality from preset names.

Suggested audition order: `af_bella`, `af_sarah`, `af_nicole`, `af_sky`, `bf_emma`, `bf_isabella`.

The requested Japanese-anime-inspired delivery remains a voice-direction target, not a capability guaranteed by these presets. Avoid arbitrary pitch shifting; it can harm naturalness and intelligibility.

## Runtime and model assets

The documented English v0.19 package contains approximately:
- `model.onnx`: 330 MB
- `voices.bin`: 5.5 MB
- `tokens.txt`
- Legacy `espeak-ng-data/` directory
- `LICENSE` and README

The current official sherpa-onnx Python example for Kokoro English uses the model, voices, tokens, and a separate US English lexicon file. It explicitly states that no eSpeak-NG data directory is needed. A second official example uses `piper_phonemize` for phonemization and also requires no eSpeak-NG data directory. Prefer the lexicon path for the first adapter to avoid adding a phonemizer dependency; separately verify the lexicon artifact's provenance, license, and hash before distribution.

The sherpa-onnx maintainer response in issue #4002 (2026-10-26) says eSpeak-NG was removed since runtime v2.0.0 and the legacy model-directory data can be deleted. This does not establish provenance or licensing for the old embedded copy. Do not redistribute that legacy directory.

The sherpa-onnx runtime repository is Apache-2.0. The Kokoro model card separately identifies the model weights as Apache-2.0. These facts do not automatically license every release archive file or binary dependency.

The official Python wheel guide lists CPU wheels for Linux x64/aarch64/armv7l, macOS x64/arm64, and Windows x64/x86. Exact Python-version compatibility, selected runtime release, native libraries, and packaging still require CI validation.

## Compliance gate

Before distribution:
- Pin exact runtime and model revisions.
- Record release URLs, archive SHA-256 hashes, file manifests, sizes, and upstream revisions for each shipped asset.
- Verify model weights, lexicon, sherpa-onnx, ONNX Runtime, and platform native libraries independently.
- Preserve licenses, notices, and attribution.
- Exclude legacy `espeak-ng-data/` from the shipped model directory.
- Keep model and lexicon assets optional and user-installed until the gate is closed.

## Performance and listening acceptance

Official documentation reports Raspberry Pi 4 real-time-factor measurements above 1.0 for the FP32 English package even with four threads. Do not extrapolate desktop performance from that result.

Measure on Windows, macOS, and Linux:
- Cold model load and warm synthesis latency.
- Real-time factor for short, medium, and long responses.
- Peak resident memory and CPU usage.
- Cancellation latency during synthesis and playback.
- Audio sample rate, channels, duration and output-size bounds.
- Repeated-session cleanup and absence of orphaned workers/device handles.
- PyInstaller packaging and clean-machine portability.
- Offline operation after optional assets are installed.

Use one fixed audition script covering a greeting, ordinary conversation, a question, a longer explanation, numbers, dates, and technical terms. Human listening review must score pronunciation, warmth, clarity, naturalness, fatigue, and consistency. User approval is required before selecting the default voice.

## Integration contract

Implement Kokoro as a separate local synthesizer adapter:
- Disabled by default.
- Implements bounded `speak_stream(chunks)` and cancellable `stop()`.
- Explicit model/lexicon paths and voice ID; no model download at app startup.
- No audio device or model initialization during desktop startup.
- Offline synthesis after assets are installed.
- Synthesis and playback run on a cancellable background worker.
- Validate PCM sample rate, channel count, sample count, duration, and memory before playback.
- Report voice selection/speed support honestly; do not claim pitch, emotion, or anime-style controls without backend support.

The adapter must pass existing output-session lifecycle and resource-limit tests before UI integration. Do not change the default backend until it passes all acceptance criteria.

## Primary references

- [sherpa-onnx Kokoro model documentation](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html)
- [Official Python Kokoro example using a lexicon (no eSpeak-NG data)](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/test-offline-tts-kokoro-en-lexicon.py)
- [Official Python Kokoro example using piper_phonemize (no eSpeak-NG data)](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/test-offline-tts-kokoro-en-phonemize.py)
- [sherpa-onnx Python wheel/platform installation](https://k2-fsa.github.io/sherpa/onnx/python/install.html)
- [sherpa-onnx TTS API](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/c-api/docs/tts.dox)
- [sherpa-onnx runtime repository and license](https://github.com/k2-fsa/sherpa-onnx)
- [Kokoro model card and weight license](https://huggingface.co/hexgrad/Kokoro-82M)
- [eSpeak-NG provenance question and maintainer response](https://github.com/k2-fsa/sherpa-onnx/issues/4002)
