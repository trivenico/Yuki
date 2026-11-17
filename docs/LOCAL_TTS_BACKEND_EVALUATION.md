# Local expressive English TTS backend evaluation

## Recommendation

Evaluate **sherpa-onnx + the official Kokoro English ONNX pack** as the next local TTS backend candidate. Do not select it as the final Yuki voice until we can audition it on supported desktop hardware and verify the exact model-pack license and provenance.

Why this is a stronger candidate than extending the current Piper/pyttsx3 adapters:

- sherpa-onnx exposes an offline TTS API and documents Kokoro as a supported model family.
- The documented English Kokoro pack includes an ONNX model, voice embeddings, tokens, and phonemizer data; the model package is materially larger than the runtime alone.
- Kokoro exposes named female English voices, and its inference path supports a speed parameter.
- ONNX Runtime is a more plausible cross-platform packaging route than introducing a full PyTorch stack into every desktop installation.

Primary evaluation candidates:
- `af_heart`: American English female baseline.
- `af_bella`: American English female alternative.
- `bf_emma`: British English female alternative.

These are female English presets, **not verified anime-style voices**. The user's requested Japanese-anime-inspired acting style remains a listening/evaluation target, not a capability claim.

## Evidence and license gates

- sherpa-onnx's official TTS API documents offline Kokoro model configuration and model/voice/token/data assets: https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/c-api/docs/tts.dox
- Official sherpa-onnx Kokoro model documentation lists the English and multilingual packs and their included assets: https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html
- The upstream Kokoro voice inventory documents voice IDs, subjective quality grades, and cautions that quality varies by voice and utterance length: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
- The model card and the sherpa-onnx release pack each need separate review. Runtime licensing does not grant model/voice rights. Preserve the pack's LICENSE, upstream model card, source revision, artifact hashes, and notices.
- Check sherpa-onnx's current license, Python wheel availability, native libraries, and supported Windows/macOS/Linux architectures at implementation time. Do not rely on third-party mirrors or repackaged weights as license authority.

## Proposed integration boundary

Implement a replaceable `SpeechEngine` adapter with:
- `load()` and `close()` lifecycle methods.
- `synthesize(text, voice_id, speed)` returning validated mono/stereo PCM metadata.
- Explicit model directory and voice ID configuration; no network model download during app startup.
- A bounded maximum input length, output duration/sample count, and synthesis timeout.
- Playback in a cancellable worker; stop must terminate current playback and prevent queued chunks from starting.
- A health/capability report that distinguishes supported speed and voice selection from unsupported pitch/emotion/style controls.
- Model manifest with source URL, revision, license, SHA-256 hashes, model size, runtime version, and notices.

The desktop UI must keep speech output opt-in and off by default. Do not automatically speak ordinary text replies unless the user activates a clearly visible voice conversation session. Text remains available if audio output is disabled or fails.

## Listening and performance acceptance

Before choosing a default voice, compare the three presets with identical short, medium, and long English prompts. Score naturalness, pronunciation, warmth, clarity, latency, memory, CPU load, and interruption responsiveness. Test Windows, macOS, and Linux package builds, including a machine without a GPU.

A human listening decision is required for the requested voice identity. Synthetic benchmarks and a model card cannot establish that a voice sounds anime-inspired or that it is the user's preferred voice.

## Not implemented by this document

No Kokoro weights or runtime are added. No speech engine, voice preset, playback UI, wake-word listener, always-on microphone, or camera feature is enabled by this evaluation plan.
