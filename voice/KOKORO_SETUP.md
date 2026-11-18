# Optional Kokoro desktop voice setup

Kokoro is an optional **offline** text-to-speech backend. Yuki does not download
voice models, open an audio device, or enable spoken replies during startup.
The first setup and every voice change are explicit user actions.

## 1. Install the optional runtime

From the repository root, install the optional packages into the same Python
environment used to launch Yuki:

```bash
python -m pip install -r requirements-kokoro.txt
```

This installs the sherpa-onnx runtime, NumPy, and sounddevice. It does not
install model weights or configure a microphone.

## 2. Obtain and verify model assets

Obtain the English Kokoro model package from the official sherpa-onnx release
or documentation. Select the English `kokoro-en-v0_19` package compatible
with the installed sherpa-onnx runtime. Extract it into a private local
directory. The configured directory must contain these files directly:

```text
<kokoro-model-directory>/
  model.onnx
  voices.bin
  tokens.txt
```

Configure the explicit English lexicon file supplied by that same model
package. Yuki intentionally fails closed if the model directory or lexicon is
missing; it does not silently fall back to a different phonemizer.

Before using or redistributing any downloaded package, inspect the license
included with that exact release and retain its notices. Verify the origin and
integrity of downloaded assets using checksums or signatures published by the
upstream release where available. The sherpa-onnx runtime license does not
automatically grant rights to model weights, lexicons, or voice data.

## 3. Configure Yuki

Set the following values in the user's private Yuki configuration. These are
local filesystem paths, not URLs:

```yaml
voice_enabled: false
tts_backend: kokoro
kokoro_model_dir: /absolute/path/to/kokoro-en-v0_19
kokoro_lexicon_path: /absolute/path/to/lexicon-us-en.txt
kokoro_voice: af_bella
kokoro_speed: 1.0
```

Use the actual lexicon filename from the downloaded package. Keep the model
directory and files writable only by the signed-in OS user. Do not place model
assets in a shared or untrusted directory.

Supported English presets currently exposed by the adapter:

| Preset | Locale/style label |
| --- | --- |
| `af_bella` | American English, feminine |
| `af_nicole` | American English, feminine |
| `af_sarah` | American English, feminine |
| `af_sky` | American English, feminine |
| `bf_emma` | British English, feminine |
| `bf_isabella` | British English, feminine |

These are upstream speaker preset labels, not a guarantee of a particular
acting style. The requested Japanese-anime-inspired English presentation is a
product preference; it is not yet a verified acoustic property of any preset.

## 4. Enable and audition safely

Keep spoken replies off until the user explicitly enables them in the desktop
session. Test short, non-sensitive sample sentences first, compare the
available presets, and adjust speed between 0.5 and 2.0. Stop speaking must
remain available during playback.

Choose the final voice only after listening on the target computer and checking
pronunciation, latency, intelligibility, and audio-device compatibility.
There is no claim of a selected or approved final voice until that audition
has happened. If Kokoro dependencies or assets are unavailable, text chat
continues to work.

## Current limitations

- This setup guide does not add voice controls to the desktop UI.
- The current adapter generates speech synchronously; it must run on a
  background worker. Stop prevents generated audio from playing after
  cancellation, but cannot forcibly interrupt the ONNX generation call itself.
- The current desktop voice controls do not yet expose Kokoro model paths,
  voice preset, or speed in a dedicated settings panel.
- No model assets are bundled with Yuki, and no real-device voice audition or
  cross-platform audio acceptance has been completed.
