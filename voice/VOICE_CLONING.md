# Voice cloning and profile lifecycle

Yuki's voice cloning is a planned, local-first capability. The first implementation
milestone is a consent-gated metadata registry; **it is not a cloning engine and
cannot yet create or play cloned voices**.

## Intended workflow

1. The user selects a source recording or records a new sample after a clear
   explanation of where it will be processed and stored.
2. Yuki requires an explicit source-rights confirmation. The source must be the
   user's own voice, a voice for which the user has documented permission, a
   properly licensed voice asset, or a synthetic voice with compatible rights.
3. A separately installed and license-reviewed local engine creates a model
   inside the user's private Yuki voice-profile directory.
4. Yuki validates the resulting model, registers metadata, and previews a short
   neutral sentence. No profile becomes active by virtue of being created.
5. The user explicitly chooses **Use for this session** or **Make default**.
   A session override never changes the saved default.
6. The user can switch back, remove profile metadata, or separately confirm
   deletion of the model files.

## Consent and abuse boundaries

- No cloning from ambient audio, calls, screen recordings, or third-party uploads
  without explicit authorization for that source voice.
- Do not clone a real person's voice to impersonate them, mislead listeners,
  bypass authentication, or facilitate fraud.
- Voice identity is not authentication. A cloned voice must never authorize
  account access, payments, approvals, or privileged desktop actions.
- Do not present synthetic speech as a real person's live speech. Use clear
  disclosure where listeners could reasonably be misled.
- Source recordings are temporary inputs to the cloning workflow, not retained
  training data by default. Explain any retention before capture/import.
- Voice model files are sensitive biometric-like artifacts. Store them under
  the signed-in user's private application directory, restrict access, and never
  sync or upload them by default.
- Deleting a profile from the registry does not delete its model directory;
  destructive file deletion is a separate explicit confirmation.

## Registry foundation

`voice/clone_profiles.py` stores profile metadata and the default profile ID in
an atomic JSON manifest. On POSIX it applies 0700 directory and 0600 manifest
permissions. It rejects model directories outside the private registry root,
requires an explicit source authorization assertion, and never accepts or stores
source audio. On Windows, directory privacy depends on inherited user-profile
ACLs; a production UI must verify/document those ACLs rather than treating
POSIX mode bits as portable security.

The registry is deliberately engine-agnostic. It does not execute model files,
load arbitrary plugins, download weights, record audio, or synthesize speech.
Engine selection, license review, model validation, preview playback, UI
consent, secure deletion, and real-device tests remain separate milestones.

## Voice command semantics

Commands such as “change your voice to [profile]” may select only a profile
already registered and preview-approved by the user. “Make this your default”
requires a confirmation step and persists the default only after confirmation.
A voice command can never create source consent or override the registry's
authorization checks.
