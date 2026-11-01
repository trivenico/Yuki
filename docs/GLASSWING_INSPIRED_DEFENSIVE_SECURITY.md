# Yuki Defensive Code Auditor — Glasswing-inspired

This Yuki-owned, non-executing static analysis component is inspired by the
defensive workflow principles in Anthropic's Project Glasswing. It does not use
Claude Mythos, claim Mythos-class capability, or copy Anthropic implementation.

## Scope

The standard-library-only Python AST scanner flags heuristic candidates:
- dynamic code execution (eval, exec);
- shell execution (os.system, subprocess shell=True);
- unsafe deserialization (pickle, unsafe YAML loading);
- disabled TLS certificate verification;
- insecure temporary file name creation;
- weak legacy hashes;
- likely hard-coded credentials.

It never executes scanned code, contacts network targets, generates exploits,
or applies patches. Findings include rule ID, severity, file, line, and
remediation guidance. False positives and false negatives are expected.

## Run

    python -m tools.defensive_code_audit . --format text --fail-on critical
    python -m tools.defensive_code_audit . --format json --fail-on critical > security-audit.json
    python -m unittest tests.test_defensive_code_audit -v

The default gate only fails on critical findings or parse errors. Teams should
triage a baseline before adopting stricter severity thresholds. Do not suppress
findings blindly; document a reviewed exception with owner and expiry.

## Next integrations

1. Add dependency vulnerability scanning (for example, pip-audit) with pinned
   tool versions and machine-readable output.
2. Add secret scanning and license/SBOM inventory.
3. Add repository-specific policy and baseline/delta mode.
4. Add isolated, read-only test execution and a human-approved patch proposal
   flow with diff review, tests, rollback, and audit events.
5. Add model-assisted triage only after deterministic scanners; treat model
   suggestions as untrusted proposals and never auto-apply them.

## Security boundary

Only scan repositories the operator owns or is authorized to assess. This tool
is an early static heuristic, not a production security certification, complete
SAST engine, vulnerability proof, or replacement for expert review.
