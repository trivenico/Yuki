# Local TTS engine evaluation: Kokoro via sherpa-onnx

Status: **candidate for a controlled desktop audition; not approved for bundling yet**

## Recommendation

Evaluate the official sherpa-onnx `kokoro-en-v0_19` package as the first higher-quality offline English TTS candidate. Keep the current Piper and pyttsx3 adapters as compatibility fallbacks until Kokoro passes packaging, licensing, performance, interruption, and listening acceptance on Windows, macOS, and Linux.

Do not bundle or redistribute model/runtime assets until the exact release artifacts and every included dependency have a documented license and provenance record.

## Candidate voice shortlist

The official sherpa-onnx English v0.19 package documents 11 speaker IDs. The female English candidates include:

| Speaker ID | Preset | Accent |
|---:|---|---|
| 1 | `af_bella` | American English |
| 2 | `af_nicole` | American English |
| 3 | `af_sarah` | American English |
| 4 | `af_sky` | American English |
| 7 | `bf_emma` | British English |
| 8 | `bf_isabella` | British English |

These are female-labelled English presets, not verified anime-style voices. The final selection must be based on actual listening tests with the same sample text, output volume, and playback device. Do not infer voice personality from preset names.

Suggested audition order:
1. `af_bella` and `af_sarah` for American English baselines.
2. `af_nicole` and `af_sky` as alternatives.
3. `bf_emma` and `bf_isabella` for British English alternatives.

The requested Japanese-anime-inspired delivery remains a voice-direction target, not a capability guaranteed by these presets. Do not apply arbitrary pitch shifting to simulate the style; it can damage naturalness and intelligibility.

## Runtime and packaging

The documented English package contains:
- `model.onnx` (approximately 330 MB in the published package listing)
- `voices.bin` (approximately 5.5 MB)
- `tokens.txt`
- `espeak-ng-data/`
- `LICENSE` and package README

sherpa-onnx exposes an offline TTS API and supports Kokoro through ONNX. The project advertises Apache-2.0 for the sherpa-onnx runtime. The model repository separately identifies Kokoro v0.19 weights as Apache-2.0. These facts do not automatically license every file in the sherpa model archive or every binary dependency.

### Compliance gate — unresolved

A sherpa-onnx maintainer issue opened on 2026-09-29 requests release-specific provenance and licensing evidence for the `espeak-ng-data` archive used by `kokoro-en-v0_19`. Treat that as an open compliance risk until the exact data source, version, license, and notices are verified.

Before distribution:
- Record exact release URL, archive SHA-256, file manifest, and upstream commit/tag for every asset.
- Inspect the archive's `LICENSE` and `README.md`; do not assume they cover all embedded data.
- Verify eSpeak-NG data provenance and licensing independently.
- Record licenses/notices for sherpa-onnx, ONNX Runtime, phonemizer/data, and all platform-specific native libraries.
- Preserve required notices and source offers/attribution.
- Keep model assets as user-downloaded optional components until the compliance gate is closed.

## Performance and acceptance plan

The official sherpa documentation reports real-time-factor measurements on Raspberry Pi 4 that remain above 1.0 even with four threads for the FP32 English package. That is a warning that lower-powered systems may not deliver real-time speech. Do not extrapolate desktop performance from this result.

Acceptance must be measured on each supported desktop OS:
- Cold model load time and warm synthesis latency.
- Real-time factor for short, medium, and long responses.
- Peak resident memory and CPU usage.
- Cancellation latency while synthesizing and while playing audio.
- Audio output sample rate, channel count, and duration bounds.
- Repeated-session resource cleanup and no orphaned worker/device handles.
- PyInstaller/desktop packaging and clean-machine portability.
- Offline operation after model assets are installed.
- Listening review for English pronunciation, warmth, clarity, naturalness, fatigue, and consistency.

Use one fixed audition script containing greeting, ordinary conversation, a question, a longer explanation, numbers, dates, and technical terms. Collect user preference ratings before choosing a default preset.

## Integration contract

Implement Kokoro as a separate local synthesizer adapter with the same narrow contract as the existing local TTS backend:
- `enabled` defaults to false.
- `speak_stream(chunks)` accepts only bounded text.
- `stop()` cancels generation and playback.
- No device or model initialization during application startup.
- No network access during speech generation.
- Explicit user opt-in and background-worker execution.
- Report the selected preset and supported controls honestly; never claim that requested pitch/expressiveness/style is supported unless the backend actually exposes it.

The adapter must pass the existing output-session lifecycle and TTS resource-limit tests before desktop UI integration. Do not change the default backend until the candidate passes the acceptance plan.

## Primary references

- sherpa-onnx Kokoro English model documentation: https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html
- sherpa-onnx TTS API and Kokoro model configuration: https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/c-api/docs/tts.dox
- sherpa-onnx repository and runtime license: https://github.com/k2-fsa/sherpa-onnx
- Kokoro model card and weight license: https://huggingface.co/MaziyarPanahi/Kokoro-82M
- eSpeak-NG provenance/licensing question for the model package: https://github.com/k2-fsa/sherpa-onnx/issues/4002
