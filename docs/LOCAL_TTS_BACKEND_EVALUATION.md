# Local expressive English TTS backend evaluation

## Recommendation

Evaluate **sherpa-onnx + the official Kokoro English ONNX pack** as the next local TTS backend candidate. Do not select it as Yuki's final voice until it passes controlled listening and performance tests on desktop hardware.

Why it is a stronger candidate than extending the current Piper/pyttsx3 adapters:
- sherpa-onnx exposes an offline TTS API and documents Kokoro support.
- The English model package includes an ONNX model, voice embeddings, and tokens.
- Kokoro provides named female English voice presets and a synthesis speed parameter.
- ONNX Runtime offers a more plausible cross-platform packaging route than adding a full PyTorch stack to every desktop installation.

## Candidate voice shortlist

The official English v0.19 pack documents 11 speaker IDs. Female English candidates:
- `af_bella` (speaker 1), `af_nicole` (2), `af_sarah` (3), `af_sky` (4): American English.
- `bf_emma` (7), `bf_isabella` (8): British English.

These are female-labelled English presets, **not verified anime-style voices**. The requested Japanese-anime-inspired delivery remains a listening target, not a capability claim. Final selection requires identical prompts and listening conditions, followed by user approval.

## Runtime, phonemization, and packaging

The published English v0.19 package listing contains approximately 330 MB `model.onnx`, 5.5 MB `voices.bin`, `tokens.txt`, and a legacy `espeak-ng-data/` directory.

The current official sherpa-onnx Python example uses a separate English lexicon with the model/voices/tokens and explicitly needs no eSpeak-NG data directory. A second official example uses `piper_phonemize` and also needs no eSpeak-NG data directory. Prefer the lexicon path for the initial adapter to avoid another runtime dependency. Verify the lexicon's own provenance, license, and hash before distributing it.

A sherpa-onnx maintainer response in issue #4002 says eSpeak-NG was removed since runtime v2.0.0 and the legacy data directory can be deleted. This does not establish provenance or licensing for the old embedded copy; exclude that directory from redistribution.

The sherpa-onnx runtime repository is Apache-2.0. The Kokoro model card separately identifies the model weights as Apache-2.0. Runtime and model licenses do not automatically cover every file in the model archive, lexicon, or native binary dependencies.

The official sherpa-onnx Python installation guide lists CPU wheels for Linux x64/aarch64/armv7l, macOS x64/arm64, and Windows x64/x86. Validate exact Python compatibility, native libraries, and PyInstaller packaging on all three desktop OSes.

## Compliance gate

Before distribution:
- Pin exact runtime/model revisions and record release URLs, archive SHA-256 hashes, file manifests, sizes, and upstream revisions.
- Verify model weights, lexicon, sherpa-onnx, ONNX Runtime, and platform native libraries independently.
- Preserve all required licenses, notices, and attribution.
- Exclude legacy `espeak-ng-data/` from the shipped model directory.
- Keep model and lexicon assets optional and user-installed until the gate is closed.

## Proposed integration boundary

Implement a replaceable local synthesizer adapter with:
- Explicit load/close lifecycle; no model load or device open at app startup.
- Explicit model/lexicon paths, speaker ID, and speed.
- Bounded text input, PCM output size/duration/sample rate/channels, and synthesis timeout.
- Cancellable synthesis and playback; stop prevents queued chunks from starting.
- Offline synthesis after assets are installed.
- A capability report distinguishing supported speed/voice selection from unsupported pitch/emotion/style controls.
- A model manifest recording source, revision, license, SHA-256, size, runtime version, and notices.

The desktop UI remains opt-in and off by default. Text chat remains available if speech is disabled or fails.

## Listening and performance acceptance

Official docs report Raspberry Pi 4 real-time-factor measurements above 1.0 for the FP32 English package even with four threads. Do not extrapolate desktop performance from this result.

Measure cold load, warm synthesis latency, real-time factor, peak memory, CPU, cancellation latency, output bounds, repeated-session cleanup, and packaged startup on Windows, macOS, and Linux, including CPU-only machines. Use identical prompts containing greetings, conversation, questions, longer explanations, numbers, dates, and technical terms. Human listening review must assess pronunciation, warmth, clarity, naturalness, fatigue, and consistency. User approval is required before selecting a default voice.

## Not implemented

No Kokoro weights/runtime, new speech engine, or final voice preset is added by this evaluation. No wake-word listener, always-on microphone, or camera feature is enabled.

## References

- https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html
- https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/test-offline-tts-kokoro-en-lexicon.py
- https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/test-offline-tts-kokoro-en-phonemize.py
- https://k2-fsa.github.io/sherpa/onnx/python/install.html
- https://github.com/k2-fsa/sherpa-onnx
- https://huggingface.co/hexgrad/Kokoro-82M
- https://github.com/k2-fsa/sherpa-onnx/issues/4002
