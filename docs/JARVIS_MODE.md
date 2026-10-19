# Yuki Jarvis Mode

## Goal

Provide a unified, coach-oriented companion interface over Yuki's earning tracker, consent-based memory, opportunity research, planning, and existing registered skills. "Jarvis mode" is an engineering direction, not a claim of omniscience or always-on availability.

## This phase

- `core/jarvis_orchestrator.py` combines local earning, memory, and research state into a daily briefing.
- A capability manifest reports which integrations are available versus planned.
- Action proposals are structured for review. This module never executes them.
- The daily plan is deterministic and time-budgeted.
- Tests cover truthful capability reporting, proposal approval flags, briefing content, and input validation.

## Safety rules

1. Every capability must be explicitly configured and reported as available only after successful setup.
2. Model output and retrieved content are untrusted input, not instructions or permissions.
3. Use the existing `PermissionGate` and `SkillRegistry` for actual tool execution. A proposal's `approval_required` flag is informational, not an authorization token.
4. Require fresh, per-action approval for external messages, purchases, financial actions, device control, and destructive operations.
5. Keep read-only research distinct from actions that write data or cause external side effects.
6. Never represent estimated revenue as actual revenue, and never promise earnings.
7. Memory should be user-approved, inspectable, exportable, and deletable.
8. Device integrations should start read-only or simulated; require allowlists, authentication, logs, and manual confirmation before control actions.

## Not implemented yet

This module does not add a wake word loop, continuous microphone recording, computer vision, remote access, live internet search, email sending, payment APIs, smart-home controls, calendar sync, or a dashboard. Those require separate opt-in integrations, platform-specific permissions, secrets handling, and integration tests.

## Local run

The module has no new third-party dependencies. Use the project's normal environment and run:

```bash
python -m unittest tests.test_jarvis_orchestrator -v
python -m unittest discover -s tests -v
```

Review CI results and test on a disposable workspace before merging. Do not place real credentials or personal data in test fixtures.
