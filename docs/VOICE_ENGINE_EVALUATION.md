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

The documented English v0.19 package listing contains:
- `model.onnx` (approximately 330 MB)
- `voices.bin` (approximately 5.5 MB)
- `tokens.txt`
- `espeak-ng-data/` in the legacy model archive
- `LICENSE` and package README

sherpa-onnx exposes an offline TTS API and supports Kokoro through ONNX. Its runtime repository is Apache-2.0. The Kokoro model repository separately identifies the model weights as Apache-2.0. These facts do not automatically license every file in a sherpa model archive or every binary dependency.

The official sherpa-onnx installation documentation lists CPU Python wheels for Linux x64/aarch64/armv7l, macOS x64/arm64, and Windows x64/x86. This is promising for the desktop target, but the exact selected release, Python version, native libraries, and packaged executable still need CI validation on all three desktop operating systems.

### eSpeak-NG archive finding

The previously open sherpa-onnx issue [#4002](https://github.com/k2-fsa/sherpa-onnx/issues/4002) is **closed**. On 2026-10-26, a sherpa-onnx maintainer stated that eSpeak-NG had been removed since sherpa-onnx v2.0.0 and that `espeak-ng-data` can be deleted from the model directory.

Implementation consequence:
- Prefer a sherpa-onnx v2.0.0-or-newer runtime and verify the exact Kokoro model configuration against that release.
- Do not package or redistribute the legacy `espeak-ng-data/` directory if the selected runtime/model path does not require it.
- Do not describe the legacy directory's provenance as resolved. The maintainer response explains that it is no longer needed with v2.0.0+, but does not provide provenance or license evidence for the old archive's copy.
- Keep the model package optional and user-installed until a release-specific manifest, hash, and license review is complete.

Before distribution:
- Record exact release URL, archive SHA-256, file manifest, and upstream revision for every asset actually shipped.
- Verify the selected model's license and notices independently from the runtime.
- Record licenses/notices for sherpa-onnx, ONNX Runtime, phonemizer/data actually used, and platform-specific native libraries.
- Preserve required notices and attribution.
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
- sherpa-onnx Python wheel/platform installation: https://k2-fsa.github.io/sherpa/onnx/python/install.html
- sherpa-onnx TTS API and Kokoro model configuration: https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/c-api/docs/tts.dox
- sherpa-onnx repository and runtime license: https://github.com/k2-fsa/sherpa-onnx
- Kokoro model card and weight license: https://huggingface.co/hexgrad/Kokoro-82M
- eSpeak-NG provenance/licensing question and maintainer response: https://github.com/k2-fsa/sherpa-onnx/issues/4002
