# Phase 269 — Artifact-bound execution receipt validation

## Delivered

### Execution receipts are now artifact-bound

`LocalVerificationExecutor.run()` accepts an optional artifact SHA-256 digest
and persists it with the execution receipt. Receipt validation now checks:
- The receipt exists in the configured execution database.
- The receipt command is the required command for the check type:
  `unit-tests` for `tests`, `security-scan` for `security`.
- The receipt argv still matches the trusted configured allowlist entry.
- The receipt workspace matches the executor workspace.
- The receipt digest matches the exact artifact digest in the report.
- The command exited with code zero and was not timed out or output-limited.

The validator fails closed for unknown check types, missing commands, malformed
references, missing receipts, stale artifact digests, and failed executions.

### Evidence ingestion validates receipts

`VerificationEvidenceStore` accepts an optional execution receipt validator.
When configured:
- Both `tests` and `security` checks must cite execution receipt references.
- Every execution receipt reference is resolved and validated before report
  persistence.
- A fabricated URI, receipt from another artifact, failed command, or wrong
  command identity is rejected.

When no validator is configured, legacy evidence-reference behavior remains
available for compatibility. Production startup must configure the executor
and validator to obtain this strict gate.

### Local runtime integration

`build_local_worker_runtime()` accepts trusted application configuration via
`verification_commands`. When supplied, it creates the executor and connects
its receipt validator to evidence ingestion. It returns the executor on the
runtime object for controlled verification workflows.

No model-generated command or argv is accepted by this API.

### Compatibility

The receipt schema migration adds the artifact digest column to existing
SQLite databases. Receipt inserts name columns explicitly, so both newly
created and migrated schemas use the same write path.

## Tests added

`tests/test_execution_receipt_evidence.py` covers:
- Valid artifact-bound unit-test and security receipts.
- Fabricated receipt identifiers.
- Receipt/artifact digest mismatch.
- Failed execution receipts.
- Required receipt references when strict validation is configured.
- Wrong command substitution for the security check.

Existing executor tests cover timeout, output cap, allowlist rejection, and
receipt persistence.

## Remaining limitations

- The configured test and security commands are supplied by trusted
  application configuration; this phase does not choose universal commands
  for every project.
- The executor remains a subprocess runner, not an OS sandbox. Network,
  filesystem, CPU, memory, and process isolation require platform-specific
  controls.
- Acceptance criteria, architecture, and claims/provenance checks still rely
  on independently authorized reviewer evidence; they are not inferred from
  command execution.
- The local runtime does not automatically run the configured commands after
  every artifact change. Orchestration of these checks is the next integration.
- Tests and CI have not been executed in this environment. No passing result
  is claimed.
