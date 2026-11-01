# Engineering evidence and verification report persistence

## Evidence collection

`automation.evidence_collector.collect_engineering_evidence` accepts an actual
`EngineeringWorkflowResult` returned by Yuki's engineering workflow and
constructs a bounded evidence bundle containing:

- workflow identifier and authenticated change actor;
- caller-supplied Git commit SHA;
- changed paths and the approved change-content digest;
- test target, exit code, timeout outcome and duration;
- hashes of captured test stdout/stderr;
- a canonical SHA-256 digest of the evidence bundle.

It rejects actor mismatches, malformed commit identifiers, inconsistent workflow
versus test outcomes, non-finite durations, oversized paths and invalid result
types. A failed or timed-out test is never marked passed.

**Provenance limitation:** the collector does not query Git or GitHub. The
commit SHA is supplied by the caller and must be independently checked against
the checked-out repository and CI run before being described as a verified
commit. Test stdout/stderr hashes bind the captured streams but do not prove
that the test process ran in a sandbox or that its output is truthful. Do not
send raw test output, secrets or unredacted workspace contents to a cloud model.

## Persistent reviewer reports

`automation.verification_report_store.VerificationReportStore` stores
structured reports in SQLite with WAL and synchronous FULL durability. It
provides save, get and bounded list operations; it intentionally exposes no
update or delete API. Each stored JSON report has its own SHA-256 integrity
digest, checked again on read. A workflow/artifact pair is immutable: an
identical save is idempotent and a conflicting second report is rejected.

Reports must remain advisory and human-approval-required. A successful report
does not authorize workspace edits, merge, deployment or release. Access
control, backup/restore, retention, encryption at rest, tamper-evident external
audit and human approval enforcement must be provided by deployment wiring.

## CI

The Phase 93 engineering workflow compiles the new modules and runs the
evidence collector, verification engine and persistent report store unit
tests. These tests do not replace a full integration test against trusted Git
and CI provenance sources.
