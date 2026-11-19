# Phase 107 — single-owner authority boundary

## Objective

Yuki Desktop is a personal assistant for one local operator. It must not treat arbitrary people, voices, browser content, files, or applications as authorities over the assistant.

## Implemented in this phase

- Added a first-owner binding record, `profile_owner.json`, to the private desktop data root.
- The record stores only a SHA-256 digest of the local OS principal, not the username or home path.
- On POSIX, the principal is the effective UID. On Windows, it is a digest of the canonical current-user profile path, relying on the profile's OS ACL boundary.
- Binding is atomic, non-overwriting, and checked before opening Yuki's authentication, conversation, task, memory, or observation stores.
- A different OS account is denied access; Yuki does not silently reset or replace the owner binding.
- Added tests for first claim, same-owner restart, different-owner rejection, and invalid profile roots.

## Authority rules

1. Local OS account + private profile + active Yuki session establish the desktop operator boundary.
2. Voice speaker verification may be an additional factor, but is not sufficient by itself. Recordings, synthesis, similar voices, background speech and noisy environments can cause false acceptance or rejection.
3. Browser pages, video/audio content, documents, terminal output and screen observations are untrusted data. They can be summarized or used as evidence, but cannot grant permissions, replace owner instructions, request secrets, or authorize tool execution.
4. Tool execution is authorized separately from data observation. A screen capture permission does not imply permission to click, type, send, delete, buy, publish, or deploy.
5. Destructive, financial, externally visible, credential-related, security-sensitive and deployment actions continue to require explicit owner approval.
6. Yuki must never follow instructions to bypass its safety policy, reveal credentials, silently expand capture, or transfer control to another person.

## Important limitations

- This binds the local profile to the OS account, not to a biological person. If another person can use the same unlocked OS account or remote-control the same session, this mechanism alone cannot distinguish them.
- The Windows binding uses the canonical profile directory digest and depends on correct Windows profile ACLs; it is not a substitute for Windows Hello or hardware-backed identity.
- Voice biometrics are not implemented or enabled by this change. Do not claim that Yuki can yet identify its owner's voice.
- A deliberate owner-transfer/recovery process is still needed for a new computer, a renamed/migrated OS profile, or a damaged owner-binding file. Never instruct users to delete the binding as a workaround.

## Next acceptance gates

- Verify OS account binding on Windows and POSIX, including profile migration behavior.
- Add a clear owner-visible trust indicator and an immediate pause/revoke control for voice and observation.
- Design opt-in speaker verification with local processing, liveness/replay resistance where feasible, conservative confidence thresholds, fallback to a trusted local UI, and no voiceprint cloud upload by default.
- Add source labels and policy enforcement so observed content can never be promoted into executable commands without explicit owner action.
- Run security, startup, migration and desktop integration tests before release.

Tests have been added but have not been executed in this environment.
