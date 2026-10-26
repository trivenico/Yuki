# Autonomous self-upgrade test mode

This is an opt-in **validation-only** mode. It does not install an upgrade into the running
application, write the candidate back to the original workspace, merge a pull request, or
promote a release.

## Contract

- Disabled unless `YUKI_AUTONOMOUS_UPGRADE_TEST_MODE=1` is explicitly set.
- Requires an application-supplied trusted-principal validator and audit sink.
- Requires a configured isolated runner factory; host test execution is always disabled.
- Automatic approvals are limited to digest-bound file edits in a disposable staging copy and
  allow-listed test modules. The existing workspace remains unchanged.
- Failed tests stop the remaining test sequence and the result is audited.
- Every result explicitly reports `promotion_allowed: false`.

## Required isolated runner

The factory receives the temporary staged workspace path and must return a runner exposing
`run(principal=..., test_module=...)`. In production, construct the existing
`PodmanIsolatedTestRunner` (or the Docker adapter after its acceptance gate) against that
exact staged copy, with a trusted digest-pinned image, rootless runtime, no network, bounded
CPU/memory/PIDs/time/output, and verified cleanup. Do not substitute a mock or host runner in
a deployed environment. The included unit-test double verifies orchestration only; it is not
evidence that OS isolation works.

## Minimal integration

The application must supply its own authenticated principal resolver and append-only audit
sink. Configure a strict path allow-list and explicit test module allow-list. Set the opt-in
environment flag only in a dedicated development/test deployment. If the flag, trusted
principal, audit sink, or isolated runner is unavailable, initialization fails closed.

## Not enabled by this change

This does **not** enable autonomous production self-upgrades, unattended GitHub merges,
restart/replacement of the live process, or automatic promotion. Those require separately
reviewed artifact integrity, rollback, runtime acceptance, and release authorization gates.
