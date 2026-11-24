# WI-5S — Inference result publication authorization

## Finding

The paired-device inference worker already authenticated the device before queue claim and re-authorized before provider execution. The job service also re-authenticated the device inside the SQLite transaction for completion, so device revocation during generation prevented result persistence.

However, central authorization was not re-evaluated at the result/error persistence boundary. An `inference:execute` allow could therefore be treated as sufficient authority to publish a result later, even if the central policy changed while the provider was running. The supervisor also reported every `PermissionError` as a device-authentication failure, even when the actual cause was a central authorization denial.

## Changes

- Re-authenticate the paired device and request a fresh `inference:complete` authorization immediately before result or sanitized-error persistence.
- Bind the completion authorization resource to a SHA-256 digest of the job ID, result digest (when present), and stable error code. Raw prompt and generated result text are not included in the audit resource.
- Keep the existing SQLite device-authentication and lease-token checks in `complete_job`; central authorization is an additional gate, not a replacement.
- A denied or unauditable completion leaves the claimed job running without a result. Normal lease expiry/reclaim handles recovery; the supervisor stops rather than retrying a revoked or denied worker.
- Rename the supervisor stop reason to `device_or_authorization_denied` so operational telemetry does not misclassify policy denials.

## Limits

The central policy check and the SQLite completion transaction are separate systems; this change narrows the authorization window but does not provide a distributed atomic transaction with a remote policy service. Device revocation and lease ownership remain serialized by the shared SQLite database transaction. Policy systems requiring strict revocation ordering must provide a transactionally checked policy revision/fence at the result store boundary. External model-provider work already in flight cannot be forcibly undone; the result is withheld if completion authorization fails.

Tests were added but not executed in this environment.
