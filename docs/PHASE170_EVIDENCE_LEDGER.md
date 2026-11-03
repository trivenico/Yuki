# Knowledge Intelligence: Evidence Ledger

## Objective

Introduce a durable, source-grounded claim workflow without treating model-extracted statements as verified facts.

## Implementation

- `knowledge/evidence_ledger.py`: SQLite claims, source chunk evidence, explicit reviews and status transitions.
- `tests/test_evidence_ledger.py`: persistence, provenance, two-source gate, conflicting evidence, reopen and stale-ID tests.
- `knowledge/__init__.py`: public API export.
- `docs/KNOWLEDGE_ENGINE.md`: operational and trust limitations.

## Trust model

Claim creation is always a proposal. Verification requires an explicit trusted caller review, two distinct canonical source IDs and no recorded refuting evidence. This is a workflow threshold, not a truth guarantee or a proof of source independence. Reviewer authentication is outside this module. Model-generated content must never invoke review operations directly.

Evidence stores chunk/source identifiers rather than source text snapshots. Source deletion or re-indexing can invalidate evidence and requires a re-evaluation workflow before claims are presented as current.

## Acceptance

The new test module covers six cases. GitHub CI on the exact branch head and independent security review are required before merge or phase completion.
