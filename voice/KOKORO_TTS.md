# Optional Kokoro local TTS adapter

`voice/kokoro_tts.py` provides an offline sherpa-onnx Kokoro adapter. It is optional and is not the default backend.

## Install

Install `requirements-kokoro.txt` only when enabling Kokoro. Install the official `kokoro-en-v0_19` model archive and an independently verified English lexicon into a user-selected local directory. Configure:
- Model directory containing `model.onnx`, `voices.bin`, and `tokens.txt`.
- Explicit English lexicon path (required; implicit phonemizer fallback is disabled).
- A supported speaker preset, initially `af_bella`, `af_nicole`, `af_sarah`, `af_sky`, `bf_emma`, or `bf_isabella`.

The adapter does not download assets, load weights, or open an audio device at construction. It loads the model on the first explicitly enabled speech request. Speech generation and playback must run on a background worker.

Install the optional Python dependencies with:

```bash
python -m pip install -r requirements-kokoro.txt
```

Set these values in Yuki's user `config.yaml` (paths are examples; use the actual local asset locations):

```yaml
tts_backend: kokoro
voice_style: japanese_anime_female_english
kokoro_model_dir: /path/to/kokoro-en-v0_19
kokoro_lexicon_path: /path/to/verified/en-lexicon.txt
kokoro_voice: af_bella
kokoro_speed: 1.0
```

The desktop app still requires the user to explicitly enable spoken replies each sign-in session. Microphone permission is separate.

## Safety and limits

- Off by default; explicit caller enablement required.
- 100 chunks / 20,000 total characters / 2,000 characters per chunk.
- PCM output limited to 25 MiB, 120 seconds, 8–96 kHz, and one or two channels.
- Playback has a bounded watchdog and `stop()` invalidates pending playback and stops the audio device.
- `close()` releases the cached model reference.
- The synchronous sherpa-onnx generation call cannot be forcibly interrupted; Stop prevents its result from playing once generation returns. This limitation is explicit.
- No network access is performed by the adapter.
- No pitch, emotion, expressiveness, or anime-style controls are claimed. The adapter exposes only preset voice selection and speed.

The current voice presets are candidates, not a verified anime-inspired voice. User listening approval and Windows/macOS/Linux packaging/performance tests are required before choosing a default.

## Licensing

Runtime, model weights, lexicon, and native dependencies have separate compliance obligations. Follow `docs/LOCAL_TTS_BACKEND_EVALUATION.md`; do not redistribute legacy `espeak-ng-data` or model assets until exact hashes, provenance, and notices are recorded.
