# Phase 127 — Guarded approved-skill runtime

Adds `core/approved_skill_runtime.py`, a facade that connects the persistent
approval and wiring service to `invoke_registered_skill()`.

## Responsibilities

- Discovers candidate manifests through the metadata-only scanner.
- Wires only explicitly approved candidates and already-instantiated trusted
  objects.
- Rejects wiring when the object's declared risk differs from reviewed metadata.
- Routes invocations through the shared guard, preserving argument bounds,
  risk-based one-shot approval, result bounds, and metadata-only audit behavior.
- Keeps registration approval separate from invocation approval.

## Example integration

A trusted bootstrapper can construct the runtime, discover manifests, review and
approve a candidate in `SkillApprovalStore`, then provide an instance from a
trusted built-in factory. Callers should use `runtime.invoke(name, argument)`
instead of calling `skill.execute()` directly.

This PR does not change `SkillRegistry.defaults()`, startup behavior, planner
dispatch, or worker dispatch. Those callers must be migrated explicitly; this
facade alone does not prove a repository-wide single execution boundary.

## Security boundaries

- No entrypoint imports or dynamic code execution occur here.
- A trusted caller must provide skill instances; Python object provenance is not
  cryptographically verified.
- Manifest permissions remain declarations, not enforced OS sandbox policies.
- Persistent registration approval does not replace per-invocation approval for
  high-risk capabilities.

## Verification

```bash
python -m unittest tests.test_approved_skill_wiring tests.test_approved_skill_runtime -v
python -m unittest discover -s tests -v
```
