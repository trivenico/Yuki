# Knowledge Intelligence: Evidence Ledger

## Objective

Introduce a durable, source-grounded claim workflow without treating model-extracted statements as verified facts. This is a knowledge subsystem workstream, not a declaration of a new serial phase; the project phase ledger still requires historical reconciliation.

## Implementation

- `knowledge/evidence_ledger.py`: SQLite claims, source chunk evidence, explicit reviews and status transitions.
- `tests/test_evidence_ledger.py`: persistence, provenance, two-source gate, conflicting evidence, reopen and stale-ID tests.
- `knowledge/__init__.py`: public API export.
- `docs/KNOWLEDGE_ENGINE.md`: operational and trust limitations.

## Trust model

Claim creation is always a proposal. Verification requires an explicit trusted caller review, two distinct canonical source IDs and no recorded refuting evidence. This is a workflow threshold, not a truth guarantee or proof of source independence. Reviewer authentication is outside this module. Model-generated content must never invoke review operations directly.

Evidence stores chunk/source identifiers rather than permanent source text snapshots. Claim reads expose whether each evidence chunk is still current and include canonical content only while it resolves. Source deletion or re-indexing can invalidate evidence; stale evidence is visible and blocks verification.

## Acceptance

The new test module covers seven cases. GitHub CI on the exact branch head and independent security review are required before merge or workstream completion.
