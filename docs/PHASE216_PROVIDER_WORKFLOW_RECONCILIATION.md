# Phase 216 — provider/workflow reconciliation bridge

## Delivered

- Added bounded, metadata-only lookup for a single provider execution.
- Added an authorized reconciliation service correlating provider execution IDs with workflow operation IDs.
- Requires provider identity to match the provider recorded on the workflow operation.
- Requires evidence source, SHA-256 digest, provider reference, resolver identity and owner permission.
- Supports retrying a partially completed two-journal success reconciliation: an identical immutable provider resolution can be verified before completing the workflow journal.
- Does not dispatch, retry, or replay provider work.

## Transaction boundary

Provider governance and workflow persistence use separate SQLite transactions. This bridge is a retryable two-step saga, not a distributed atomic transaction. If the provider journal commits and the workflow journal fails, the operation remains unresolved until the same evidence is re-submitted and verified. Conflicting evidence is rejected.

## Trust boundary

The caller remains responsible for authenticating the provider lookup and validating evidence provenance. A digest proves byte identity only. Production deployment still needs a trusted provider-specific evidence verifier and operational monitoring for partially reconciled records.
