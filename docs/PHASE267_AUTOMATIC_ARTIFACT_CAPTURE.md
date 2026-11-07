# Phase 267 — Automatic artifact capture and local verification wiring

## Delivered

### Coordinator artifact capture

When an acceptance store is configured, coordinator refresh now:
- Registers an acceptance record for each succeeded specialist task.
- Reads the bounded text output from the task result.
- Encodes the exact output as UTF-8 bytes.
- Persists the artifact with plan, subtask, task, author, byte length, and SHA-256.
- Attaches that artifact to the acceptance record.
- Leaves the task in `awaiting_verification`; it does not invent or auto-approve a reviewer report.
- Fails closed if artifact capture or attachment fails.

The coordinator integration test now checks that a completed predecessor is
automatically captured and remains blocked until a matching independent report
is submitted and accepted.

### Local runtime wiring

`build_local_worker_runtime()` now constructs and returns:
- `VerificationEvidenceStore`
- `VerificationWorkflow`
- `EngineeringAcceptanceStore`

The coordinator receives the acceptance store, so the normal local runtime
uses acceptance-gated dependency dispatch rather than leaving the feature
unconfigured.

The local operator identity is authorized to submit verification reports and
perform acceptance transitions. This is an operator-mediated review boundary,
not proof of independent reviewer execution. Agent identity separation is
still enforced by report validation, but real reviewer tasks and authenticated
reviewer identities remain future work.

## Important limitations

- Only bounded text output is captured; structured files, patches, and binary
  artifacts need a separate artifact adapter.
- Reports are not generated automatically. A trusted test/review executor must
  produce evidence before the operator links it.
- The local operator can submit reports; this does not itself establish that
  tests or security scans actually ran.
- Multiple SQLite stores are used. There is no cross-database atomic commit;
  recovery/reconciliation remains required.
- No test or CI execution is claimed in this phase.

## Next

Add a controlled local verification executor that runs only configured,
allow-listed checks in an isolated workspace, records command identity,
exit status, output digest, and tool version, and produces evidence records
that cannot be marked passed without a successful actual execution.
