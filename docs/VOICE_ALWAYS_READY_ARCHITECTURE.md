# Always-ready companion voice and desktop protection

## Status

Architecture target only. The current voice implementation supports explicit, bounded recording and selected-file transcription. It does not yet implement a live microphone listener, wake-word detection, always-ready mode, duplex conversation, or proactive desktop monitoring.

The desktop application remains the primary product. This design must integrate with the authenticated desktop conversation runtime; it must not create a parallel identity, memory, or authorization system.

## Product behavior

Yuki should feel like a familiar, warm, attentive AI companion. The owner may select an affectionate companion relationship style (including a girlfriend-like conversational tone). This is a persona and interaction preference, not a claim that Yuki is human, conscious, or emotionally dependent. The style must never weaken safety, privacy, consent, or authorization rules.

The target hands-free experience is:

1. **Always-ready local standby:** while the owner has explicitly enabled hands-free mode and granted OS microphone permission, a lightweight local audio pipeline detects a wake phrase such as "Hey Yuki". A future optional direct-address detector may also identify likely speech directed to Yuki, but must be conservative and configurable.
2. **Activation:** wake detection changes the visible state and opens a bounded conversation capture window. A sound/visual acknowledgement is optional.
3. **Conversation:** speech is transcribed and passed to the authenticated conversation runtime. Yuki responds through the configured TTS engine.
4. **Follow-up window:** configurable voice activity and turn detection allow follow-up questions without repeating the wake phrase. A timeout, explicit stop phrase, mute, or OS permission loss returns the listener to standby or off.
5. **Standby again:** only the local wake detector remains active. No idle audio is retained or sent to remote services.

Always-ready means local acoustic processing is active; it does **not** mean continuous recording, continuous transcription, or continuous cloud upload.

## Explicit user controls

- Hands-free mode is opt-in, off by default on first install.
- OS microphone permission is mandatory; application preference cannot override OS denial.
- A persistent, prominent microphone/listening indicator distinguishes Off, Standby (local wake detection), Capturing conversation, Processing, Speaking, and Muted.
- Provide a global mute control that immediately stops microphone capture, including a keyboard shortcut and an in-app control.
- Provide settings for wake phrase, sensitivity, language, follow-up timeout, quiet hours, output device, and whether proactive spoken suggestions are enabled.
- Show a clear first-run explanation of local standby processing, conversation capture, any remote processing, and retention.
- No hidden listener, stealth mode, automatic restart after user mute, or microphone use after logout.
- Shared-device and bystander privacy must be considered; make it easy to disable hands-free mode in shared spaces.

## Audio data lifecycle

- Standby audio is processed in memory in short bounded frames by the local wake detector.
- Do not write standby frames to disk, create a rolling audio archive, or send them to cloud APIs.
- Clear transient audio buffers after each detector operation and on mute, logout, shutdown, or error.
- Only after activation may a bounded conversation segment be captured and transcribed.
- Conversation audio retention is off by default. If a future feature offers saving recordings, it requires a separate explicit setting and visible disclosure.
- Do not use voice recognition or speaker similarity as the sole authentication or authorization factor. Voice can be replayed, synthesized, or spoofed.
- Remote speech/model processing must be disclosed and governed by the same provider, data, and consent policies as typed conversations.

## Runtime architecture

Keep the listener behind small interfaces so device capture, wake-word engines, and VAD implementations can be replaced without changing the desktop UI or conversation service.

- `AudioInput`: opens a selected microphone only after permission and explicit hands-free enablement; reads bounded frames; closes reliably.
- `WakeWordDetector`: local-only frame consumer returning a detection event and confidence; no network or filesystem capability.
- `VoiceActivityDetector`: identifies speech boundaries during an activated turn.
- `ConversationCapture`: owns a bounded in-memory buffer with hard duration/byte limits and cancellation.
- `SpeechTranscriber`: converts only activated audio into text.
- `AuthenticatedConversationAdapter`: submits text through the existing owner/session-checked desktop runtime.
- `SpeechOutput`: speaks the response and supports interruption/stop.
- `VoiceSessionController`: owns the explicit state machine and coordinates start, stop, timeout, errors, logout, and shutdown.
- `VoiceStatusPresenter`: exposes state to the desktop UI; it never owns microphone access.

No audio callback may call the model/provider, access conversation storage, or update Qt widgets directly. Audio capture and inference run on bounded workers; UI updates are marshalled onto the UI thread.

## State machine

```text
OFF -- owner enables + OS permission --> STANDBY
STANDBY -- wake phrase --> CAPTURING
CAPTURING -- end of speech / stop / max duration --> PROCESSING
PROCESSING -- transcript accepted --> THINKING
THINKING -- response ready --> SPEAKING
SPEAKING -- finished / interrupted --> FOLLOW_UP
FOLLOW_UP -- speech within timeout --> CAPTURING
FOLLOW_UP -- timeout --> STANDBY
ANY STATE -- mute / logout / shutdown / permission loss --> MUTED or OFF
MUTED -- explicit owner unmute --> STANDBY (only if still enabled and permitted)
```

All transitions must be explicit, testable, and idempotent. Errors fail closed to Muted or Off; they must never silently resume capture.

## Companion context and memory

- Persona style is stored as a user preference and applied only as conversational guidance.
- Persisted memory remains governed by the existing memory consent and secure-memory mechanisms.
- Do not infer or store sensitive personal details from ambient audio without an explicit, user-visible memory action.
- Provide inspect, correct, and delete workflows for remembered preferences and facts.
- Do not treat background conversations, media playback, or other people's speech as instructions to Yuki.
- Wake-word detection is not consent to execute a computer action. Actions still pass through existing authorization, policy, and approval gates.

## Proactive desktop protection

A separate, least-privilege monitoring service may collect narrowly scoped security and health signals after explicit setup. It should start with read-only checks and explain each finding.

Initial signals may include:
- OS security-center / firewall / antivirus status where supported;
- application update and restart requirements;
- resource anomalies such as sustained CPU, memory, disk, or temperature warnings when reliable sensors are available;
- suspicious startup entries or newly observed executable changes;
- unusual network/listening-port changes using OS-supported metadata;
- failed login or security events only where the OS exposes them safely.

Safety boundaries:
- no keylogging, credential scraping, packet-content inspection, covert screenshots, or indiscriminate file scanning;
- no automatic process termination, file deletion, firewall changes, privilege escalation, or security-setting changes;
- explain evidence, uncertainty, impact, and a safe next step;
- require explicit approval for changes, and use OS-native security tools where possible;
- never claim that Yuki guarantees protection or replaces a maintained endpoint-security product;
- monitoring must be independently disableable from voice listening.

## Bounded improvement and self-evolution

Yuki may learn preferences through consented memory updates and improve software through a controlled engineering pipeline:

observe -> diagnose -> prepare proposed change -> isolated branch/worktree -> tests and security scans -> human approval -> staged rollout -> health checks -> rollback on regression.

Runtime voice audio must not directly train or modify models, prompts, policies, permissions, or executable code. No self-granted permissions, unrestricted shell access, or automatic production self-modification.

## Acceptance gates

1. Hands-free is off by default and cannot start without OS permission plus explicit enablement.
2. Local wake detection works without network access; tests prove the detector path makes no network or file writes.
3. No idle audio is persisted or uploaded; tests inspect all audio sinks and cleanup paths.
4. Wake phrase activates a bounded capture session; false wake events return safely to standby.
5. Maximum capture duration and memory use are enforced even if VAD fails.
6. Mute, logout, shutdown, device removal, and permission revocation stop capture promptly and clear buffers.
7. Repeated wake phrases, noisy input, playback feedback, interruptions, and concurrent UI actions do not create duplicate sessions.
8. Voice commands do not bypass authenticated identity, tool authorization, approval, or audit boundaries.
9. Audio-device and detector failures fail closed and are visible to the owner.
10. Cross-platform tests cover Windows, macOS, and Linux; physical microphone acceptance is required before release.
11. Proactive protection is read-only initially, independently disableable, and produces no automatic destructive changes.
12. No production claim is made until privacy, security, reliability, and clean-device acceptance gates pass.

## Delivery sequence

1. Complete native desktop packaging and the explicit recording security foundation.
2. Integrate explicit desktop voice capture/transcription with the authenticated conversation runtime.
3. Add a local wake-word adapter and testable voice-session state machine.
4. Add VAD, bounded follow-up conversation, TTS interruption, and mute/quiet-hour controls.
5. Add opt-in, read-only desktop health/security monitoring.
6. Add approved remediation workflows and controlled improvement only after the monitoring and authorization layers are proven.

## Wake-word engine selection

Do not hard-code a vendor or model in the conversation layer. Before selecting an engine, verify its current license, model-weight license, offline behavior, supported platforms, CPU use, false-accept/false-reject behavior, update mechanism, and whether commercial redistribution is permitted. Keep model assets versioned and checksummed. No model or audio asset is accepted solely because its wrapper package has a permissive license.
