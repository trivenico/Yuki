# Phase 54 — Provider runtime hardening

## Gap
The Phase 53 Gemini CLI adapter bounded input/output and deadlines, but inherited the entire Yuki process environment. That could expose unrelated application credentials (for example cloud-provider keys or database URLs) to the CLI and any subprocesses it launches.

## Change
The adapter now builds a minimal environment from an explicit allowlist: executable lookup, home/config resolution, temporary directories, basic locale/terminal settings, and selected TLS certificate paths. Arbitrary API keys, cloud credentials, database URLs, and other unrelated variables are not forwarded.

## Operational impact
- Official Gemini CLI account authentication must be configured in the same OS user's home/config location.
- Environments that rely on proxy variables or custom authentication environment variables may need a deliberate, reviewed extension to the allowlist. They are not forwarded implicitly.
- The executable is still resolved using the parent process PATH before launching with the filtered environment.
- This reduces environment leakage but is not a sandbox for a compromised CLI binary.

## Validation
A regression test asserts that required runtime variables are preserved and common application secrets are excluded. The branch must pass all CI jobs before merge. No live model request is part of CI.
