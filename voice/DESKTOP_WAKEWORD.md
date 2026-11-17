# Desktop Hey Yuki wake-word controls

The desktop chat screen exposes an optional local wake-word session when the
user has explicitly enabled the microphone for the current authenticated
session and selected a local ONNX model.

## User flow

1. Sign in to Yuki.
2. Enable the existing microphone permission toggle. This is still separate
   from spoken-reply permission.
3. Choose a local `.onnx` wake-word model supplied by the user.
4. Select **Start Hey Yuki listening** and confirm the dedicated consent
   dialog.
5. Yuki processes bounded 16 kHz mono PCM frames locally. The audio source
   keeps at most one volatile frame; it does not write idle audio to disk,
   create transcripts, or upload frames.
6. When the detector triggers, Yuki stops and releases the wake-word
   microphone session before starting the existing five-second explicit
   capture. The recognized transcript is inserted into the composer for
   review. It is not automatically sent.
7. **Stop listening**, microphone disable, sign-out, and application shutdown
   signal capture cancellation immediately. Device joins and terminal cleanup
   run on a background worker so the desktop interface does not wait for an
   audio thread. A closed session cannot restart itself.

Wake-word detection does not grant permission to execute tools, control the
computer, or approve an action. Any such action still follows its own
authentication and approval gates.

## Model and optional dependencies

Install the optional dependencies from
`requirements-wakeword-openwakeword.txt` in the desktop environment. Select
a locally obtained ONNX model. Yuki does not download model weights or bundle
a pretrained model.

The openWakeWord code is Apache-2.0, but upstream pretrained models have
separate licensing terms. Do not use or redistribute pretrained weights
unless their exact license permits the intended use. For commercial
distribution, use a custom model trained from assets with documented
commercial training and redistribution rights.

## Current limitations

- The desktop controls are an integration foundation, not a hardware-verified
  release. Microphone behavior must be tested on supported Windows, macOS,
  and Linux devices.
- The first custom Hey Yuki model has not yet been trained or acoustically
  evaluated.
- Device permission prompts and format support are operating-system and
  driver dependent. If the exact 16 kHz mono PCM16 input cannot be opened,
  the listener fails closed.
- Wake-word listening is intentionally opt-in and is not automatically
  restarted after sign-out, shutdown, or a device error.
