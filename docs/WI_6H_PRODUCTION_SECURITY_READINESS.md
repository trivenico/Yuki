# WI-6H — Production security readiness probes

## Delivered

- A dedicated `ProductionSecurityReadinessASGI` health application that requires a validated `ProductionSecurityContext`.
- Liveness remains process-level and independent from external audit-anchor availability.
- Readiness re-verifies the authorization audit chain against the fresh independent checkpoint on every request and returns 503 on any failure.
- Health responses are deliberately generic and use no-store/no-cache and nosniff headers; unsupported routes and methods are rejected.
- Deployment guidance specifies private health-listener mounting and explicitly states which infrastructure properties this probe does not establish.

## Why a separate health application

The public chat application and its host integration are not modified to expose health or administrative routes. A deployment must mount the probe on a private health listener and inject the exact same trusted security context used by the runtime. This keeps the health surface narrow and avoids implying that application code can enforce network segmentation.

## Tests added

- Healthy context returns 200 readiness.
- Tampered audit chain returns 503 readiness while liveness remains 200.
- Unsupported methods and paths are rejected.

Tests are committed but have not been run in this environment.

## Not provided by this work item

- KMS/HSM provisioning or signing-key rotation automation.
- A multi-host replay/revocation database or multi-region audit backend.
- TLS/reverse-proxy configuration, firewall rules, private listener provisioning, or Kubernetes/cloud deployment.
- Backup restore drills, monitoring/alert delivery, or CI execution.

The existing production launcher intentionally continues to refuse strict production/public startup unless deployment-owned code supplies a validated `ProductionSecurityContext`. Do not bypass that gate by changing environment flags.
