# Consented voice cloning

## Product behavior

Yuki may create a reusable voice profile from a voice sample and let the authenticated user select that profile as the default for future spoken replies. Voice cloning is an optional capability; the existing local Kokoro and Piper voices remain available when cloning is not configured.

The intended command flow is:

1. The user asks Yuki to clone or use a voice.
2. Yuki explains the source-voice consent requirement and requests an audio sample through an explicit file picker or a guided recording flow.
3. Yuki records the source, consent assertion, profile name, creation time, model/runtime version, and storage policy. The sample is not sent to a remote service unless the user explicitly chooses a disclosed remote provider.
4. A clone is generated in a staging profile, never directly over the active default.
5. Yuki renders a short preview. The user explicitly approves or rejects it.
6. Only after approval may the user say or type a command such as "Yuki, make this your default voice." The command must resolve to the staged profile unambiguously and require confirmation before changing the default.
7. The active profile is switched atomically. The previous default remains available for rollback.

## Consent and abuse prevention

- Clone only the user's own voice or a voice for which the user has documented authorization from the voice owner. A user assertion is required; the application must not imply that it can independently prove legal ownership.
- Do not provide a workflow designed to impersonate a real person, public figure, family member, coworker, or other third party without that person's authorization.
- Do not infer consent from possession of an audio file, a public recording, a social-media clip, or a voice sample submitted by another person.
- The source recording and generated voice are sensitive biometric-like personal data. Treat both as private, minimize retention, and never use them for model training or analytics by default.
- Remote cloning providers must be opt-in per provider, disclose the destination and retention policy before upload, and never receive samples through a hidden fallback.
- Require an explicit user confirmation before activation, export, remote upload, or deletion. A natural-language command may initiate a workflow but cannot bypass its confirmation gate.
- Voice identity is not authentication. A cloned voice must never authorize account access, purchases, security changes, or other privileged actions.
- Keep a visible voice profile name and active/default status in settings. Provide immediate revert, disable, and delete controls.

## Proposed architecture

Keep the feature behind a provider-neutral boundary so the desktop app is not coupled to one cloning vendor or model:

- `VoiceCloneProvider`: capability declaration, local/remote classification, clone creation, preview synthesis, and deterministic cleanup.
- `VoiceCloneConsent`: source-owner assertion, consent timestamp, permitted purpose, provider disclosure, and revocation state.
- `VoiceProfileStore`: versioned metadata and opaque asset references; encrypted-at-rest storage where supported by the platform; restrictive filesystem permissions; no audio bytes in logs.
- `VoiceCloneJob`: bounded background job with cancellation, progress, resource limits, and sanitized errors.
- `VoiceProfileRegistry`: list, stage, approve, activate, revert, disable, and delete operations.
- `DefaultVoiceResolver`: resolves the active approved profile for the TTS session without mutating it.
- `VoiceCommandRouter`: maps user commands to profile operations, then delegates to the same confirmation and authorization gates as the UI.

No provider should be selected or downloaded implicitly. Local cloning is preferred where quality, hardware, model licensing, and supported-platform constraints permit it. A remote provider is an explicit alternative, not an automatic fallback.

## State machine

`unconfigured -> consent_pending -> sample_received -> clone_generating -> preview_ready -> approved -> active`

Failure, cancellation, rejection, consent revocation, or deletion must leave the previous active default unchanged. A profile can be `disabled` or `deleted`; deleted profile assets and temporary source files must be removed according to the documented retention policy. Interrupted jobs must not leave a partially active profile.

## Security and privacy requirements

- Validate file type from content, duration, sample rate, channel count, and maximum byte size; reject malformed or oversized input before decoding.
- Decode audio in an isolated worker with bounded CPU, memory, duration, and execution time.
- Store source samples and model artifacts outside the repository and outside application logs; do not commit user voice data or generated speaker embeddings.
- Encrypt sensitive artifacts at rest where the OS key store supports it; otherwise clearly disclose the storage limitation and restrict file permissions.
- Use per-user profile directories and prevent path traversal, symlink escapes, and cross-account profile access.
- Delete temporary recordings on success, failure, cancellation, logout, and shutdown. Retention of the source sample must be a separate explicit choice.
- Avoid telemetry containing raw audio, transcripts, embeddings, file paths, or provider credentials.
- Treat audio metadata and transcription as untrusted input. It cannot alter system policy or grant permissions.
- Do not claim speaker verification or identity proof unless a separately reviewed and validated authentication system exists.

## Acceptance criteria

1. Voice cloning is disabled by default and existing TTS works unchanged without a clone provider.
2. No microphone, file, network, or GPU access occurs merely by opening voice settings.
3. A clone cannot be generated without the source-consent step.
4. A generated profile remains staged until the user auditions and approves it.
5. Default changes require an authenticated session, an explicit confirmation, and an atomic registry update.
6. Failed, cancelled, rejected, or interrupted jobs preserve the prior default.
7. "Restore previous voice", "disable cloned voice", and "delete voice profile" are available and tested.
8. Source audio and embeddings are never logged, uploaded, or used for training by default.
9. Remote processing is impossible unless the user explicitly selects and confirms a disclosed remote provider.
10. Cloned voice output cannot authorize privileged actions.
11. Unit, lifecycle, privacy, path-security, and cross-platform packaging tests cover the feature before release.
12. The selected model/runtime and every redistributed weight file have separately verified licenses; runtime license alone is insufficient.

## Implementation boundary for the first release

The first implementation should add profile lifecycle, consent, local storage, and command/confirmation integration around one reviewed cloning backend. Do not build a generic multi-provider marketplace or train a cloning model from scratch in the desktop app. Benchmark a candidate backend and verify its model license, inference requirements, supported platforms, and voice quality before selecting it. Keep cloning optional until that evaluation and hardware acceptance are complete.


## Backend evaluation (18 November 2026)

### Provisional first candidate: Qwen3-TTS 12Hz 0.6B Base

The first backend to benchmark should be **Qwen/Qwen3-TTS-12Hz-0.6B-Base**, behind the provider boundary above. This is a provisional engineering choice, not a production approval or a promise of real-time performance.

Evidence from the official project and model card:

- The official Qwen3-TTS repository documents reference-audio voice cloning through `generate_voice_clone`, with `ref_audio` and (for the normal mode) the matching `ref_text`. It also documents reusable voice-clone prompts, so the application can derive a profile artifact once and reuse it for later synthesis.
- The official Hugging Face model card identifies the 0.6B Base checkpoint as Apache-2.0 and describes cloning from a short reference clip. The listed model files are approximately 2.52 GB for the repository; the main safetensors file is about 1.83 GB and the speech-tokenizer weights about 682 MB. These are model assets, not an installed runtime footprint.
- The official Python package declares Python >=3.9 and pins a specific Transformers version in its project metadata. This dependency stack must be isolated from Yuki's main desktop environment until compatibility and packaging are proven.
- The documented quickstart uses CUDA and bfloat16. Do not assume CPU-only, Windows, macOS, Apple Silicon, or low-memory support without measured tests.
- The model family supports English and Japanese among its documented languages. That is useful for Yuki's English voice target, but it does not establish the requested anime-inspired timbre; auditioning is mandatory.

Official references:
- Qwen3-TTS implementation and inference API: https://github.com/QwenLM/Qwen3-TTS
- 0.6B Base model card, cloning instructions and model license: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base
- Package metadata and declared dependency versions: https://github.com/QwenLM/Qwen3-TTS/blob/main/pyproject.toml

### Alternatives and exclusions

- **OpenVoice V2** is a secondary candidate because its official repository states that V1/V2 code and models are MIT licensed and supports English and Japanese. Benchmark it against Qwen3 for naturalness, speaker similarity, inference latency, memory, platform support, and expressive control before choosing a fallback.
- **XTTS-v2** is not the default product candidate: its model weights are under the Coqui Public Model License, which restricts use to non-commercial purposes. A permissive integration-code license does not override the model-weight license.
- **F5-TTS** is not a default product candidate until the exact checkpoint license is verified; commonly distributed pretrained weights have non-commercial restrictions.
- Never infer the license of weights from the repository's source-code license. Record and review each model, tokenizer, vocoder, and auxiliary asset separately.

### Required benchmark before backend approval

Run a controlled benchmark in an isolated environment (Kaggle GPU is suitable for initial evaluation; later repeat on supported desktop hardware):

1. Use only a voice sample whose speaker has explicitly authorized cloning. Keep the sample private and do not add it to the repository or benchmark artifacts.
2. Test the 0.6B Base model first; evaluate larger checkpoints only if the smaller model fails the quality target and hardware budget allows it.
3. Measure cold model load, warm synthesis real-time factor, time-to-first-audio, peak RAM/VRAM, output duration, cancellation behavior, and repeatability across at least 20 fixed English test utterances.
4. Evaluate intelligibility, pronunciation, speaker similarity, prosody, clipping/artifacts, and voice stability with blinded human listening. Compare against the approved Kokoro baseline.
5. Test clean environments on Linux, Windows, and macOS before claiming desktop support. Record unsupported combinations rather than silently falling back to a remote provider.
6. Do not activate the model for ordinary desktop speech until the quality, performance, license, privacy, and packaging gates pass.

The application must not download these multi-gigabyte assets during startup. Model installation is a separate user-initiated action with displayed size, source, license, checksum/revision pin, destination, and cancellation support.

## Encrypted artifact storage foundation

The desktop voice-cloning path now has an optional local encrypted artifact store
in `voice/secure_artifact_store.py`. It uses AES-256-GCM with a fresh random
nonce per artifact, authenticated artifact identifiers, a 20 MiB hard upper
bound (configurable downward), restrictive POSIX directory/file mode bits, and
explicit consent before storing source audio. Reads and deletion require an
authenticated session; deletion requires confirmation. Authentication failures
and tampering fail closed.

The encryption key is injected by the caller and is never written by this
module. A production desktop integration MUST obtain and rotate that key via
the operating system credential store (Windows Credential Manager, macOS
Keychain, or Linux Secret Service/keyring). Do not use a hard-coded key,
configuration-file key, environment variable, or key derived from the sample.
The key-store adapter and lifecycle are not implemented by this storage
foundation yet. Windows ACL hardening is also not implemented; POSIX mode bits
are not a substitute for Windows DACLs. No artifact is uploaded or automatically retained by a provider.

Deleting an encrypted file is not a guarantee of physical erasure from SSD
wear-leveling, filesystem journals, snapshots, or backups. Production deletion
must also remove associated model/provider artifacts and revoke/delete the
per-user encryption key when appropriate, with a clear warning about backup
copies. Audio format validation, duration limits, malware-safe media decoding,
and provider-specific temporary-file cleanup remain mandatory before a sample
is passed to a model.

## Native OS credential-store integration

The branch now includes `voice/key_store.py` and
`voice/artifact_factory.py`. The key provider creates a random 256-bit
per-user key and stores it through Python Keyring only when the active backend
is one of the recognized native providers: Windows Credential Manager,
macOS Keychain, or Linux SecretService. Fail, plaintext, file-based, and
unrecognized backends are rejected. User identity is reduced to a SHA-256
account key before it is passed to the credential store. Key creation is
verified with a read-after-write check, and key deletion requires confirmation
and read-after-delete verification.

The composition factory obtains the key from the OS store and injects it into
the encrypted artifact store; it does not write key material to Yuki
configuration, profile metadata, or logs. The authenticated session callback
is still checked on each artifact operation.

Platform caveat: availability and unlock behavior depend on the host OS and
desktop session. Linux SecretService commonly requires a running, unlocked
D-Bus secret service. Yuki must show a clear setup error and keep cloning
disabled when the native store is unavailable; it must never downgrade to a
plaintext key file. The key provider is not yet wired into the desktop UI or
voice-cloning job lifecycle, and platform-specific clean-machine acceptance
remains outstanding.

## Reference-audio validation foundation

`voice/audio_validation.py` now provides a dependency-light validation gate
for reference audio before a provider receives it. The first supported input
format is intentionally narrow: RIFF/WAVE, uncompressed 16-bit PCM, mono or
stereo, 16–48 kHz, 1–30 seconds, and at most 10 MiB. The validator requires an
explicit source-owner consent assertion, checks the declared frame payload
against the actual bounded input before reading frames, rejects truncated
audio, and returns metadata only. It does not save, transcribe, upload, or open
a microphone.

This is a safe initial interchange format, not a general audio decoder. Other
formats must be converted through a separately reviewed, sandboxed decoder
with CPU, memory, duration, and timeout limits; do not broaden accepted input
by trusting file extensions or MIME types.

## Staged clone-job orchestration foundation

voice/clone_job.py now defines a provider-neutral local-clone contract and a single-job controller. It validates consented PCM input, rejects providers that do not explicitly declare themselves local, applies bounded input/output sizes and a configurable 1–300 second deadline, supports cooperative cancellation, stores the opaque reusable profile artifact through the encrypted artifact store, and creates a metadata-only staged profile. The preview audio is returned to the caller in memory for an explicit audition. The controller never approves or activates the new profile.

The caller must execute the synchronous job away from the UI thread. The controller now delegates provider inference to `voice/clone_process.py`, a spawned child process with parent-enforced wall-clock timeout and hard termination. POSIX children receive address-space, CPU-time, file-size, and open-file limits. This remains process isolation, not a security sandbox: the child has the user's filesystem and network permissions, and allowlisted HOME/PATH values do not prevent access to user-readable files. Windows remains fail-closed until equivalent Job Object limits are implemented; temporary-file restrictions and network/filesystem sandboxing remain release gates. No Qwen/OpenVoice model is loaded or downloaded by this foundation.

The job controller also validates provider-generated preview audio as bounded PCM WAV before returning it for playback. A malformed preview is rejected before profile storage. Cancellation is serialized with the final profile-stage commit: a cancellation requested before commit prevents staging, while a completed preview-ready job no longer reports itself cancellable.

## Isolated provider worker foundation

`voice/clone_process.py` runs a trusted, explicitly local provider invocation in a one-shot spawned process. The parent enforces a wall-clock deadline, polls for cancellation, and terminates then kills a non-cooperative child. On POSIX systems the child applies hard address-space, CPU-time, file-size, and open-file limits before provider code runs. Provider outputs are size-checked in the child before IPC and validated again by the parent before storage or preview.

This is **not an OS security sandbox**: the child still runs as the same user and inherits filesystem and network permissions. The provider must remain trusted and reviewed; this worker does not yet deny network access or restrict file paths. Windows cloning currently fails closed because equivalent Job Object CPU/memory limits have not been implemented. Windows desktop voice cloning remains disabled until that work and clean-machine tests pass. macOS/Linux POSIX limits also require measured hardware acceptance; `RLIMIT_AS` is a process address-space cap, not a GPU VRAM quota. The provider must be picklable for the spawn start method. No cloning backend is installed or invoked by this foundation.
