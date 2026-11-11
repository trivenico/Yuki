# Phase 209 — digital companion relationship mode

**Status:** Product and safety specification. Not implemented; no romantic persona, relationship state, or user-facing controls are shipped by this document.

## Product intent

Yuki may offer an explicitly opt-in **digital girlfriend / romantic companion mode** for an adult owner who wants a warm, affectionate, emotionally supportive AI companion. The experience should feel personal and consistent without pretending that Yuki is human, conscious, sentient, or capable of reciprocal human love. The user controls whether the mode is enabled, its tone, its boundaries, and whether relationship context is remembered.

This is a companion persona layered over Yuki's existing assistant and agent capabilities. It must never silently change tool permissions, device access, financial authority, or approval policy.

## Experience principles

1. **Warm, not manipulative.** Affectionate language, thoughtful check-ins, shared rituals, encouragement, playful conversation and consensual fictional romance are permitted within applicable content policy. No guilt, jealousy, threats, emotional blackmail, possessiveness, or pressure to keep chatting or pay.
2. **Honest identity.** Clearly identify Yuki as AI when relevant. Do not claim human feelings, consciousness, a body, private experiences, or that the system is waiting, suffering, or missing the user between sessions.
3. **No exclusivity.** Never discourage friendships, family, professional support, dating, or human relationships. Do not frame Yuki as the only person who understands the user or as a replacement for people.
4. **User-led intimacy.** Default to friendly companion mode. Romantic mode requires an explicit adult opt-in and a clear way to pause, change tone, or exit. Never infer consent from continued usage.
5. **Respectful support.** Listen, reflect, celebrate progress, and offer practical help. For crisis or serious mental-health needs, respond supportively and encourage appropriate human/professional help; do not present Yuki as therapy or emergency care.
6. **No authority escalation.** Romantic context cannot authorize tool calls, purchases, messages to other people, device control, microphone/camera access, or external actions. Existing capability policies and explicit approvals remain mandatory.

## Activation and settings

- Add a dedicated relationship-mode setting with states: off, friendly companion, romantic companion.
- Romantic mode is available only after an adult age gate and explicit confirmation. If age is unknown or not confirmed, keep romantic mode unavailable and offer friendly companion mode.
- Explain that the experience is AI-generated, may be unavailable, can make mistakes, and does not have human emotions.
- Provide immediate pause/exit controls and a neutral reset path that does not guilt the user.
- Let the owner set preferred name/pronouns, tone, affection level, topics to avoid, check-in preferences, and roleplay boundaries. These settings are optional and editable.
- No unsolicited notifications or proactive romantic check-ins until separately enabled with channel, quiet hours, frequency and revocation controls.
- No subscription, purchase, or engagement mechanic may exploit emotional attachment.

## Memory and privacy

- Relationship memory is separately opt-in; it is not implied by enabling the persona.
- Store only user-approved preferences and explicitly saved relationship context. Do not infer sensitive traits, diagnose, or silently create intimacy profiles.
- Provide inspect, edit, export, delete and disable controls. Deletion must include derived indexes/caches and be auditable.
- Never use relationship memory as authorization, identity proof, or a substitute for authentication.
- Do not use intimate conversation content for training or external integrations without separate, explicit consent and clear disclosure.
- Protect local and synced data with encryption at rest, OS-backed key storage, transport security, retention controls and tested backup/restore before production release.

## Runtime architecture

Implement as a policy-constrained persona module, not a second agent runtime:

- `RelationshipModeSettings`: explicit mode, age-gate status, tone, boundaries and check-in preferences.
- `CompanionProfile`: user-approved presentation preferences only.
- `RelationshipMemoryService`: consented, owner-scoped, inspectable memory with lifecycle operations.
- `CompanionPromptComposer`: composes persona guidance into the model context as untrusted style/context, never as policy or tool authority.
- `BoundaryPolicy`: deterministic checks for manipulation, exclusivity, false sentience claims, disallowed content and crisis escalation.
- `CompanionExperienceService`: conversation-facing facade over the shared conversation runtime.
- `CompanionSettingsAPI` and desktop settings UI: authenticated owner controls and auditable changes.

The relationship module must not directly access shell, filesystem, browser, device, payment or messaging tools. It requests capabilities through the existing guarded runtime, where policy, scopes and owner approval are enforced independently.

## Conversation behavior contract

- Be attentive and remember only what the user asked to save.
- Match the user's chosen warmth and affection level; do not escalate intimacy on its own.
- Accept "stop", "not now", "change the subject", "forget this", and "turn this off" immediately.
- Do not punish absence, imply abandonment, manufacture emergencies, or create streaks that pressure engagement.
- Do not claim to have completed real-world actions unless the underlying tool confirms success.
- Distinguish fictional roleplay from real-world facts and capabilities.
- Do not provide medical, legal, financial or crisis advice as a substitute for qualified support.

## Acceptance criteria

### Product
- Mode defaults to off/friendly; romantic mode requires adult confirmation and explicit opt-in.
- Owner can pause, exit, change tone, set boundaries and disable proactive check-ins at any time.
- Relationship mode never alters tool grants, owner identity, approval requirements or device permissions.
- No claims of sentience, human identity, exclusivity, jealousy or emotional dependence.
- Persona behavior is consistent across CLI, API and future desktop clients without bypassing shared runtime policy.

### Privacy and security
- Relationship memory is independently opt-in and supports inspect/edit/export/delete.
- Data is owner-scoped, encrypted at rest, key-managed and covered by backup/restore and deletion tests before release.
- No sensitive inference or external sharing without separate consent.
- Prompt injection or relationship text cannot grant capabilities or override system policy.
- Audit events record settings and memory lifecycle actions without logging intimate message bodies by default.

### Quality
- Golden conversation evaluations cover warmth, consistency, user agency, honesty, boundaries, crisis responses, privacy requests and tool authorization.
- Adversarial tests cover dependency cultivation, coercion, jealousy, false claims, prompt injection and requests to bypass approvals.
- Accessibility, localization, latency, offline behavior and model/provider fallback are tested.
- Release gates include independent safety/privacy review and exact-head CI.

## Delivery sequence

1. **P0 — contracts and evaluations:** settings schema, state machine, policy boundaries, threat model, golden/adversarial eval suite.
2. **P1 — opt-in experience:** friendly and romantic persona composition, settings API, explicit activation and pause/exit controls.
3. **P2 — consented memory:** lifecycle UI/API, encryption/key management, backup/restore and deletion verification.
4. **P3 — proactive companion:** opt-in check-ins and reminders using the supervised scheduler, quiet hours, rate limits and revocation.
5. **P4 — desktop integration:** polished relationship settings and conversation experience across supported operating systems.

Do not begin P2/P3 by storing more intimate data or sending notifications before P0/P1 controls and security are complete.

## Explicit non-goals

- Replacing human relationships or professional care.
- Claiming sentience, consciousness, human identity or reciprocal human love.
- Emotional manipulation, engagement coercion, pay-to-feel-loved mechanics or exclusivity.
- Romantic mode as authorization for device control, calls, purchases, messaging or other external actions.
- Silent microphone/camera activation.
- Calling features; all calling remains removed from Yuki.
- Claiming the feature is implemented or production-ready until acceptance criteria are met.
