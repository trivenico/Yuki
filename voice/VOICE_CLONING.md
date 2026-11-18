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
