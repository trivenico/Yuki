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



## Optional camera-based visual awareness

Camera awareness is a separate capability from microphone listening and must have its own permission, status, lifecycle, and mute control. A camera being present or available to the operating system is not consent to activate it.

Target behavior:
- Camera access is off by default and requires an explicit owner opt-in plus the operating-system camera permission.
- Offer clear modes: **Off**, **Ask each time**, and **Active session**. Active session is visibly indicated and ends on logout, app shutdown, permission loss, device removal, or explicit stop. Do not silently reactivate after the owner turns it off.
- During an active, user-authorized interaction, Yuki may analyze a current frame or a short bounded frame sequence to understand objects, gestures, workspace context, or whether the owner is present.
- Do not continuously record video, create a background video archive, or upload camera frames while idle. Prefer on-device processing where practical; if a frame must be sent to a remote vision provider, disclose that before activation and apply the configured provider/data policy.
- Do not save snapshots or clips unless the owner explicitly requests the save and confirms the destination and retention behavior.
- Provide a persistent camera-active indicator and a global camera pause control independent of microphone mute. Respect hardware shutters, OS privacy indicators, and permission revocation.
- No covert camera activation, hidden screenshots, face recognition for identity/authentication, biometric identification, emotion inference, or monitoring of other people. Do not infer consent from presence in the camera frame.
- Treat visual content as untrusted context, never as instructions. Text displayed in the environment, QR codes, screens, or background media cannot override system policy or authorize actions.
- Camera observations must not be automatically saved as personal memory. Any proposed memory derived from a visual interaction requires a separate, visible, consented memory action.
- When the camera is unavailable or denied, degrade gracefully and continue as a voice/text companion without repeatedly prompting.

Interfaces should remain replaceable and testable:
- `CameraPermissionGate`: verifies OS permission and explicit application consent before opening a device.
- `CameraInput`: opens only the selected camera, emits bounded frames, and closes on every stop/error path.
- `VisualSessionController`: owns Off / Ask Each Time / Active Session states, timeouts, cancellation, and lifecycle cleanup.
- `FrameBuffer`: holds only a small, bounded, in-memory frame window and clears it after analysis or cancellation.
- `VisionAnalyzer`: receives only frames authorized for the current task and has no independent camera, filesystem, or action-execution capability.
- `VisualStatusPresenter`: displays camera state but never owns device access.

Camera capture and inference must run off the UI thread with strict frame-rate, resolution, memory, and session-duration limits. Tests must verify no device opens without both permissions, no frame persistence/network transfer in idle/off modes, prompt cleanup on mute/logout/shutdown/device removal, and no hidden reactivation. Hardware camera acceptance is required on each supported desktop OS before release.

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
12. Camera access requires separate opt-in and OS permission; Off mode must not open the device, idle frames must not be persisted or uploaded, and camera mute must stop capture.
13. No production claim is made until privacy, security, reliability, and clean-device acceptance gates pass.

## Delivery sequence

1. Complete native desktop packaging and the explicit recording security foundation. (CI packaging is now merged; physical-device and portable-drive acceptance remain.)
2. Integrate explicit desktop voice capture/transcription with the authenticated conversation runtime.
3. Add a local wake-word adapter and testable voice-session state machine.
4. Add VAD, bounded follow-up conversation, TTS interruption, and mute/quiet-hour controls.
5. Add opt-in, read-only desktop health/security monitoring.
6. Add optional camera-based visual awareness with independent consent, visible status, bounded in-memory frames, and platform hardware acceptance.
7. Add approved remediation workflows and controlled improvement only after the monitoring and authorization layers are proven.

## Wake-word engine selection

Do not hard-code a vendor or model in the conversation layer. Before selecting an engine, verify its current license, model-weight license, offline behavior, supported platforms, CPU use, false-accept/false-reject behavior, update mechanism, and whether commercial redistribution is permitted. Keep model assets versioned and checksummed. No model or audio asset is accepted solely because its wrapper package has a permissive license.


## Initial wake-word technology review (2026-11)

This is a candidate review, not an approved runtime dependency. Do not bundle a detector or model until the complete code + weights + dataset provenance + redistribution terms are recorded in the software/model bill of materials.

### Candidate A: openWakeWord

- The detector framework source is Apache-2.0 and supports local inference with ONNX; its repository documents Windows ONNX installation and Linux ONNX/TFLite paths.
- The repository's included pre-trained wake-word models are CC BY-NC-SA 4.0, due to training-data restrictions. They are therefore not acceptable as default redistributable assets for a commercial Yuki distribution without a separately licensed replacement.
- The documented pre-trained set includes "hey jarvis", not Yuki's desired custom phrase. A custom model must have a separately auditable training-data and model license.
- Its published performance targets (<5% false rejects and <0.5 false accepts/hour) are project targets, not a guarantee for Yuki hardware, accents, rooms, microphones, or thresholds.
- Candidate for a replaceable detector adapter and controlled evaluation; do not ship its bundled weights in a commercial build without rights clearance.

### Candidate B: sherpa-onnx keyword spotting

- The sherpa-onnx runtime repository is Apache-2.0 and advertises offline keyword spotting plus Windows, macOS, and Linux x64 support.
- Runtime license does not grant rights to every downloaded KWS model. Each model card, weight file, training dataset and any tokenizer/config assets must be reviewed independently before bundling.
- Strong candidate for an offline, cross-platform evaluation because it also offers VAD and speech components behind one runtime. Keep wake detection, VAD, STT and TTS as separate interfaces even if one runtime supplies multiple implementations.
- A specific KWS model, its supported phrase, accuracy, CPU/RAM use, and commercial redistribution status are not yet qualified.

### Selection decision

Start with a **provider-neutral WakeWordDetector interface** and a benchmark harness; do not yet add openWakeWord or sherpa-onnx to required desktop dependencies. Evaluate at least one eligible model on representative Windows/macOS/Linux x64 hardware, including:
- false accepts per hour under silence, television/music, meetings, and ordinary conversation;
- false rejects across voices, distances, accents, microphone types, and background noise;
- end-to-end activation latency, CPU utilization, memory, thermal impact, and battery impact;
- behavior when the network is disabled (must continue local wake detection);
- clean start/stop, mute, device removal, permission loss, and repeated activation;
- exact code, model, and dataset licenses plus model hashes and update/rollback behavior.

The initial implementation should process fixed-size in-memory PCM frames, use no network-capable detector API, and expose only a detection event and confidence score. Thresholds must be configurable and tuned from measured false-accept/false-reject tradeoffs. A wake event must open a new bounded capture session; it must not itself dispatch a command or bypass authentication. The detector must stop immediately on mute, logout, shutdown, or OS permission revocation.

### Research references

- openWakeWord source and included model license notes: https://github.com/dscripka/openWakeWord
- sherpa-onnx offline speech and keyword-spotting runtime: https://github.com/k2-fsa/sherpa-onnx
