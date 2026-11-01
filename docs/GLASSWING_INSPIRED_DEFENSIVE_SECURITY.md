# Yuki Defensive Code Auditor — Glasswing-inspired

This Yuki-owned defensive workflow is inspired by the defensive engineering
principles of Anthropic's Project Glasswing. It does not use Claude Mythos,
claim Mythos-class capability, or copy Anthropic implementation.

## Components

### Python source auditor

Run:

    python -m tools.defensive_code_audit . --format text --fail-on critical
    python -m tools.defensive_code_audit . --format json --fail-on critical

The standard-library AST scanner flags heuristic candidates for dynamic
execution, shell execution, unsafe pickle/YAML deserialization, disabled TLS
verification, insecure temporary paths, weak hashes, and likely hard-coded
credentials. It never executes scanned code or contacts network targets.

### Redacted secret auditor

Run:

    python -m tools.secret_audit . --format text
    python -m tools.secret_audit . --format json

This bounded heuristic scanner checks text files up to 1 MB, skips common
generated/vendor directories and symlinks, and emits only a rule, path, line,
and short SHA-256 fingerprint. It never prints matched credential values.
A finding should be treated as sensitive: inspect it only in an authorized
environment, rotate confirmed exposed credentials, and remove them from source
history according to the organization's incident process.

## CI

The defensive audit workflow runs regression tests, produces static-analysis
and redacted-secret reports, and uploads short-lived artifacts. Repository
checks are heuristic; they do not establish that the repository is free of
vulnerabilities or secrets. Findings require secure human triage.

## Next integrations

1. Dependency vulnerability scanning using a pinned, machine-readable tool
   such as pip-audit; distinguish advisory matches from exploitability.
2. SBOM generation and license inventory.
3. Repository-specific policy and reviewed baseline/delta mode.
4. Real isolated, read-only test execution with CPU, memory, process, time,
   filesystem and network restrictions.
5. Model-assisted triage after deterministic scanners. Treat model suggestions
   as untrusted proposals; require independent review and human approval.
6. Patch verification with tests, diff review, rollback and audit events.

## Security boundary

Only scan repositories the operator owns or is authorized to assess. These
scanners are heuristic triage aids, not complete SAST, secret-scanning,
vulnerability proof, or a security certification. Never run untrusted project
code on the host as part of an audit.
