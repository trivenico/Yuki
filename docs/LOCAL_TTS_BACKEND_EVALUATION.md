# Local expressive English TTS backend evaluation

## Recommendation

Evaluate **sherpa-onnx + the official Kokoro English ONNX pack** as the next local TTS backend candidate. Do not select it as the final Yuki voice until we can audition it on supported desktop hardware and verify the exact model-pack license and provenance.

Why this is a stronger candidate than extending the current Piper/pyttsx3 adapters:

- sherpa-onnx exposes an offline TTS API and documents Kokoro as a supported model family.
- The documented English Kokoro pack includes an ONNX model, voice embeddings and tokens. The legacy archive also lists eSpeak-NG data, but sherpa-onnx maintainers state that eSpeak-NG has been removed since runtime v2.0.0 and that the legacy `espeak-ng-data` directory can be deleted.
- Kokoro exposes named female English voices, and its inference path supports a speed parameter.
- ONNX Runtime is a more plausible cross-platform packaging route than introducing a full PyTorch stack into every desktop installation.

Primary evaluation candidates:
- `af_bella`: American English female.
- `af_nicole`: American English female.
- `af_sarah`: American English female.
- `af_sky`: American English female.
- `bf_emma`: British English female.
- `bf_isabella`: British English female.

These are female English presets, **not verified anime-style voices**. The requested Japanese-anime-inspired acting style remains a listening/evaluation target, not a capability claim.

## Evidence and license gates

- sherpa-onnx's official TTS API documents offline Kokoro model configuration: https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/c-api/docs/tts.dox
- Official sherpa-onnx Kokoro model documentation lists English and multilingual packs and their assets: https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html
- The official Python wheel guide lists CPU wheels for Linux x64/aarch64/armv7l, macOS x64/arm64, and Windows x64/x86: https://k2-fsa.github.io/sherpa/onnx/python/install.html
- The upstream Kokoro model card identifies the model weights as Apache-2.0: https://huggingface.co/hexgrad/Kokoro-82M
- sherpa-onnx runtime licensing is Apache-2.0: https://github.com/k2-fsa/sherpa-onnx
- Maintainer response in issue #4002 (2026-10-26) states eSpeak-NG was removed since sherpa-onnx v2.0.0 and the legacy data directory can be deleted: https://github.com/k2-fsa/sherpa-onnx/issues/4002

The eSpeak-NG response does **not** establish provenance or licensing for the old embedded copy. Do not redistribute that legacy directory. For the selected v2.0.0+ runtime/model combination, verify the exact configuration and ensure the removed data is not required.

The model card and the sherpa-onnx release pack each need separate review. Runtime licensing does not automatically grant model/voice rights or clear every file in a release archive. Preserve the selected pack's LICENSE, upstream model card, source revision, artifact hashes, and notices.

## Proposed integration boundary

Implement a replaceable local speech-engine adapter with:
- Explicit `load()` and `close()` lifecycle methods.
- Synthesis with explicit voice ID and speed selection.
- Explicit model directory and voice ID configuration; no network model download during app startup.
- Bounded input length, output duration/sample count, and synthesis timeout.
- Playback in a cancellable worker; stop must terminate current playback and prevent queued chunks from starting.
- A health/capability report that distinguishes supported speed and voice selection from unsupported pitch/emotion/style controls.
- Model manifest with source URL, revision, license, SHA-256 hashes, model size, runtime version, and notices.

The desktop UI must keep speech output opt-in and off by default. Text remains available if audio output is disabled or fails.

## Listening and performance acceptance

Before choosing a default voice, compare candidate presets with identical short, medium, and long English prompts. Score naturalness, pronunciation, warmth, clarity, latency, memory, CPU load, and interruption responsiveness. Test Windows, macOS, and Linux package builds, including a machine without a GPU.

A human listening decision is required for the requested voice identity. Synthetic benchmarks and a model card cannot establish that a voice sounds anime-inspired or that it is the preferred voice.

## Not implemented by this document

No Kokoro weights or runtime are added. No new speech engine, voice preset, playback UI, wake-word listener, always-on microphone, or camera feature is enabled by this evaluation plan.
