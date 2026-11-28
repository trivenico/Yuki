# Docker security-option diagnostic parsing audit

**Review date:** 2026-11-28  
**Branch:** `research/reference-projects-integration-assessment`

## Finding: substring matching can produce false-positive security diagnostics

In `core/sandbox_runtime.py::parse_docker_info`, the parser derives reported security flags with substring checks such as `"name=rootless" in item`, `"name=seccomp" in item`, and equivalent checks for AppArmor and SELinux.

This is not token-aware parsing. For example, an entry such as `"not-name=rootless"` or `"xname=seccomp"` contains the searched substring and is classified as reporting that option, despite not having the expected `name=<value>` token. The parser then exposes these booleans through `DockerDaemonDiagnostics`.

The module correctly states that Docker's self-reported metadata is diagnostic only and not attestation. This finding concerns diagnostic accuracy and operator interpretation; it does not establish an execution bypass or that these values authorize sandbox launches.

## Impact

Malformed or unexpected SecurityOptions text can create false-positive diagnostic flags, potentially causing operators or downstream reporting to believe a security feature was reported when it was not.

## Required remediation

1. Parse comma-separated option tokens and compare the exact `name=<expected>` token after trimming whitespace and normalizing case.
2. Reject or explicitly preserve malformed option tokens rather than treating substring matches as valid reports.
3. Add negative tests for prefixed/suffixed lookalikes (for example, `not-name=rootless`, `xname=seccomp`) and positive tests for actual Docker token forms.
4. Keep the explicit distinction between daemon-reported metadata and independently verified runtime attestation.

No runtime code changed, no Docker daemon was contacted, no container was created or removed, and no local tests were run.
