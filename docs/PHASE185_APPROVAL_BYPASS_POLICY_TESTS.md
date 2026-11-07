# Phase 185 — Verify explicit specialist approval-bypass grant

## Test hardening

Extends the Phase 184 authorization regression to cover both sides of the policy: plan creation alone cannot disable approval, while a host authorization policy that explicitly grants `agent:plan_without_approval` can create a no-approval subtask.

The test exercises plan persistence and the recorded approval requirement. It does not claim that all future worker actions are safe without approval; production hosts should grant this privilege only to narrowly scoped principals and retain action-specific controls.

## Validation

The test is part of `tests/test_agent_coordinator.py`. GitHub Actions for the exact commit must complete before this change is considered CI-validated.
