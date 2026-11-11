# Phase 210 — relationship mode settings contracts

**Status:** Initial implementation on PR #722. This phase is a configuration and persona-guidance foundation only; it does not enable a shipped girlfriend experience.

## Implemented

- `RelationshipModeSettings` with modes off, friendly and romantic.
- Defaults are off, with relationship memory and proactive check-ins disabled.
- Romantic mode fails closed unless the owner explicitly confirms adulthood.
- Memory and proactive check-ins are independent opt-ins and cannot be enabled while mode is off.
- Proactive check-ins require explicit quiet-hour boundaries.
- Affection level and bounded topic-avoidance preferences.
- Persona guidance is presentation context only and explicitly cannot grant tools or override policy, permissions or approvals.
- Unit tests cover defaults, adult gate, modes, opt-ins, quiet-hour validation, bounded preferences and authority separation.

## Not implemented

No settings API/UI, durable storage, age verification service, relationship-memory store, model-runtime integration, scheduler, notifications or desktop experience. A boolean adult attestation is a product gate, not identity or age verification; production rollout requires an appropriate age-assurance and privacy review. Settings are not yet persisted.

## Next gates

1. Integrate settings through the shared companion runtime without injecting style context as policy.
2. Add authenticated owner controls and persistence with audit and deletion semantics.
3. Add golden/adversarial conversation evaluations.
4. Complete age assurance, privacy and safety review before enabling romantic mode.
