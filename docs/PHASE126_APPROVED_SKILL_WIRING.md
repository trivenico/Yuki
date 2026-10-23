# Phase 126 — Persistent approval and controlled skill wiring

Adds `core/approved_skill_wiring.py` to connect metadata discovery to an explicit
approval store and an already-instantiated set of trusted skill objects.

## Flow

1. Discover manifests using `discover_skill_manifests(root)`.
2. Review the candidate and explicitly record approval using
   `SkillApprovalStore.approve(candidate, approved_by=...)`.
3. The trusted bootstrapper supplies instantiated skill objects by name.
4. `ApprovedSkillWiringService.wire_approved(...)` wires only candidates whose
   exact manifest SHA-256 has a non-revoked approval and whose supplied instance
   passes name/interface checks.
5. Revoke approval with `revoke(name, manifest_sha256)`.

## Safety boundaries

- This service never imports or executes an entrypoint.
- The service does not create skill instances; callers must supply trusted,
  separately instantiated objects.
- A changed manifest has a different digest and requires a new approval.
- Missing approval, missing instance, or revoked approval means no registration.
- The SQLite approval database must be stored in a protected location with
  filesystem permissions appropriate to the operator identity.
- This is not an OS sandbox. It does not verify the Python source bytes behind
  an already-instantiated object, nor enforce the manifest's permission list at
  runtime. Do not pass untrusted instances.

## Verification

```bash
python -m unittest tests.test_metadata_skill_discovery tests.test_approved_skill_wiring -v
python -m unittest discover -s tests -v
```

## Not yet wired into startup

This phase adds a controlled service and persistent approval records, but does
not replace `SkillRegistry.defaults()`, auto-load third-party code, or make all
task planner and worker paths use the guarded runtime. Those require dedicated
integration work and tests.
