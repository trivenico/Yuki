# Local wake-word session foundation

Yuki's wake-word path is deliberately separate from explicit speech capture.

## Current implementation

`voice/wakeword_session.py` provides a lifecycle/privacy controller with injected
microphone and detector adapters. It is not yet a hardware microphone driver or
a selected wake-word model.

- Construction does not instantiate a detector or open a microphone.
- Only an explicit `start()` call constructs adapters and opens the source.
- State transitions are observable: disabled, starting, listening, stopping,
  triggered, failed, and closed.
- Audio adapters return bounded 16 kHz mono signed PCM16 frames (at most 16 KiB each).
- Adapter/model initialization is run from a background worker, never the desktop GUI thread.
- Frames are passed directly to the detector and are not persisted, logged,
  transcribed, queued, or added to memory by this controller.
- The detector adapter must run inference locally and must not make network
  requests. The controller itself has no networking or file-writing behavior.
- A wake callback should enqueue a signal to the UI/application event loop. It
  must not run transcription or an agent task on the audio thread.
- Stop interrupts a blocked source read, joins the worker with a bounded
  timeout, and releases the microphone and detector. Close is terminal.
- No automatic restart occurs after a wake, failure, logout, or shutdown.

## Not yet implemented

- The optional `OpenWakeWordDetector` accepts one user-supplied ONNX model,
  uses 16 kHz mono PCM16 in 1280-sample (80 ms) frames, and requires a
  configurable consecutive-frame threshold. It loads only after explicit
  session start.
- The `SoundDeviceWakeWordAudioSource` captures engine-sized PCM16 frames
  into a one-frame volatile queue; actual hardware and OS permission behavior
  still need desktop testing.
- A legally redistributable "Hey Yuki" model.
- Desktop settings, OS permission integration, tray indicator, and mute UI.
- Real-device false-accept/false-reject testing and CPU usage measurements.

## Engine and model licensing decision

The openWakeWord **code** is Apache-2.0, but its bundled pre-trained models
are CC BY-NC-SA 4.0. The default models therefore must not be bundled into a
commercially distributable Yuki build without resolving those model terms.
The available pretrained list includes "hey jarvis", not "hey yuki"; English
is currently the supported language. See the upstream README:
https://github.com/dscripka/openWakeWord

An optional openWakeWord ONNX adapter is now implemented for one explicit
user-supplied model path. The upstream v0.6.0 runtime code is Apache-2.0, but
pretrained model assets have separate terms and are not bundled. Yuki will
train a custom "Hey Yuki" model using the Kaggle runbook at
`training/wakeword/README.md`. The controller remains engine-agnostic and
does not download models automatically. Before shipping, record separate
licenses/provenance for runtime, model, training data and generated asset.

## Privacy acceptance criteria

Before enabling a real listener in the desktop UI:

1. Microphone permission and a separate Yuki opt-in are required.
2. Listening is visibly indicated and has a global immediate mute/stop.
3. Idle audio is processed locally and discarded frame-by-frame.
4. No idle audio is written to disk, sent to cloud providers, or retained in
   conversation history.
5. Logout, application exit, device removal, permission loss and OS sleep stop
   the listener; restart requires a fresh explicit user action.
6. Wake detection only opens the existing bounded conversation path. It does
   not itself authorize tool execution or computer control.
7. Hardware tests measure false activations per hour, missed activations,
   CPU/RAM use, and recovery from microphone errors on Windows, macOS and Linux.

The user has no desktop hardware available for microphone acceptance testing
yet, so CI tests validate lifecycle contracts only—not real acoustic quality.
