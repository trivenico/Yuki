# Yuki voice identity and TTS selection

## Product voice target

Yuki's default spoken voice is:
- feminine voice identity;
- English output with clear, natural pronunciation;
- Japanese-anime-inspired voice acting: warm, lively, expressive, affectionate, and gently playful;
- conversational rather than exaggerated, shrill, childish, or robotic;
- stable identity across greetings, ordinary conversation, alerts, and spoken task updates.

Pitch, speaking rate, expressiveness, and emotional intensity are user-adjustable preferences. The voice must remain intelligible and comfortable over long sessions. Keep an accessibility option for a more neutral delivery.

The profile describes a style, not an identity claim. Do not clone or imitate a specific actor, streamer, celebrity, or copyrighted character. Do not claim that a static voice embedding can perform every emotion or acting direction.

## Initial local TTS candidate: Kokoro-82M

Kokoro-82M is the first candidate for a local English TTS benchmark:
- The upstream Hugging Face model card declares Apache-2.0 for the model weights and describes an 82-million-parameter open-weight TTS model.
- Its published English voice inventory includes feminine American English voices such as `af_heart` and `af_bella`, and feminine British English voices such as `bf_emma` and `bf_isabella`.
- The upstream voice catalogue describes subjective voice-quality grades; these are not objective guarantees and do not establish an anime acting style.
- The model provides a practical local baseline, but the final voice must be selected by listening tests on supported desktop hardware. A voice's gender label does not guarantee perceived gender or the requested character style.

Candidate comparison order:
1. `af_heart`: initial American-English female baseline.
2. `af_bella`: alternate American-English female voice with a different timbre.
3. `bf_emma`: British-English female alternative if the owner prefers UK pronunciation.

Do not silently set one candidate as the final production voice until the owner has heard and approved it. The profile currently selects the style preference only; it does not pin a voice model.

## Model and runtime supply-chain gates

Before bundling or downloading a TTS model:
- record the exact model repository, immutable revision, file hashes, model-card license, and attribution requirements;
- verify each voice embedding and any phonemizer, G2P package, runtime, tokenizer, and native library independently;
- avoid arbitrary pickle loading from untrusted model sources; use safe model formats or restricted loading where available;
- verify offline inference, supported OS/CPU architectures, startup time, real-time factor, memory, latency, and output-device behavior;
- keep model files outside the signed application executable where practical, with checksum verification and a documented update/rollback path;
- do not upload text or audio to a remote service unless the owner has enabled and been informed about that provider.

The current desktop TTS adapter supports Piper and pyttsx3, not Kokoro. Kokoro is a researched candidate only; integration requires a separate implementation, dependency review, packaging work, and cross-platform tests.

## Evaluation protocol

Use a fixed consented phrase set covering greetings, questions, explanations, numbers, names, abbreviations, emotional but non-sensitive examples, and long-form listening. Compare the candidate voices at identical text, output volume, and playback device.

Score:
- perceived feminine voice quality and owner preference;
- English pronunciation and intelligibility;
- warmth, naturalness, expressiveness, and anime-inspired character feel;
- fatigue and comfort after a 15-minute listening session;
- first-audio latency, real-time factor, CPU/RAM, and thermal impact;
- interruption latency and clean shutdown.

Keep objective performance measurements separate from subjective preference. The owner approves the default voice after hearing the actual output. Until then, describe Kokoro as a candidate, not the finished Yuki voice.

## References

- Official model card and Apache-2.0 model declaration: https://huggingface.co/hexgrad/Kokoro-82M
- Official voice inventory and voice descriptions: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
