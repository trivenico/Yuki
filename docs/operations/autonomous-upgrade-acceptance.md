# Autonomous upgrade acceptance runbook

This runbook is for maintainers validating the isolated self-upgrade test path. It is not authorization to enable production self-upgrade promotion.

## Safety rule

Keep autonomous upgrade promotion disabled until all required gates below have passed and an independent security reviewer has recorded the result. A green pull-request CI run does not prove that the container runtime is isolated.

## Required protected GitHub configuration

Configure these settings through repository administration before running acceptance:

1. Protect `main` with required review and status checks. Do not use a feature-branch run as release evidence.
2. Register a dedicated, disposable Linux runner with the labels `self-hosted`, `linux`, and `yuki-rootless-podman`. Do not share it with untrusted repositories or general-purpose workloads.
3. Configure the protected environment `yuki-rootless-podman-acceptance` with required reviewers and deployment branch restrictions limited to `main`.
4. Set the environment variable `YUKI_TEST_WORKER_IMAGE` to an approved image reference matching `docker.io/library/python:3.11-slim@sha256:<64 lowercase hex characters>`. Review the image provenance and digest before setting it. Do not use a mutable tag alone.
5. Ensure the runner's Podman daemon/runtime is rootless and that the runner host is disposable/reprovisionable after each acceptance attempt.

The workflow does not configure any of these repository/runner protections itself. Do not store credentials in the image variable.

## Run the protected acceptance workflow

1. Merge the reviewed workflow and integration-test changes to protected `main` only after required code review and normal CI pass.
2. In GitHub Actions, select **Phase 137 rootless Podman isolation** and dispatch it against `main`.
3. Confirm the workflow verifies the rootless runtime and pulls the configured digest-pinned image.
4. Save the workflow URL, commit SHA, image digest, runner identity, and job logs with the release evidence.

A workflow that is skipped, runs from another ref, uses an unapproved image, or does not actually execute the integration test is not acceptance evidence.

## Acceptance evidence to inspect

- The positive isolation contract test completes successfully in the real rootless Podman runtime.
- A deliberately failing candidate returns non-zero, is recorded as failed in the audit trail, and does not produce any promotion authorization.
- Container cleanup is confirmed after success, test failure, timeout, and runtime error. A cleanup error must fail closed and be visible to the operator.
- The worker cannot access the network, host secrets, writable host paths, or the Docker/Podman control socket. Confirm the effective runtime configuration in logs and review the host-side threat boundary; unit tests alone are insufficient.
- The candidate artifact is staged without following symlinks or allowing path traversal, and the tested artifact identity matches the artifact under review.
- Rollback and audit persistence are verified before any promotion capability is considered.

## Stop conditions

Stop and keep promotion disabled if the image digest is not approved, the runtime is not rootless, any test is skipped unexpectedly, cleanup cannot be confirmed, audit records are missing, the candidate artifact identity changes, or any reviewer raises an unresolved security concern.

## Enablement policy

This acceptance path is test-only. Do not turn on unattended production upgrades as a side effect of a successful test. Any later production rollout needs a separate reviewed change, explicit operator approval, staged rollout, rollback plan, monitoring, and an independently reviewed security boundary.
