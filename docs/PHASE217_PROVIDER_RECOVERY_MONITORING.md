# Phase 217 — provider recovery monitoring

- Added a global, bounded recovery queue for running and uncertain provider executions.
- Added stable keyset pagination by (updated_at, execution_id).
- Added append-only recovery-attempt audit records with optional workflow operation correlation.
- Audit entries contain identifiers and reason codes only, never prompts, credentials or provider request payloads.
- Listing and auditing do not call providers, resolve executions, cancel requests or retry work.
- Workflow and provider journals remain separate; correlation must use explicit identifiers, never inferred timestamps or provider names.
