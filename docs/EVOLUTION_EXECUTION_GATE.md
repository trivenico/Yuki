# Evolution execution gate

`automation.evolution_execution_gate.EvolutionExecutionGate` is a fail-closed
preparation boundary between approved evolution plans and a sandbox runtime.

## Guarantees

- Requires a stable plan ID, request ID, and SHA-256 plan digest.
- Calls an injected approval verifier with the exact plan ID, digest, request,
  and authenticated actor. Only the literal boolean `True` is accepted.
- Approval verifier exceptions fail closed.
- Requires an existing worktree below a configured workspace root.
- Rejects the workspace root itself and paths resolving outside it.
- Requires a validated `SandboxPolicy`, explicit workspace-write capability,
  and disabled networking.
- Bounds command argument count and size; arguments remain an argv sequence,
  not a shell command string.
- Returns a frozen `SandboxExecutionSpec`.

## Deliberate non-goals

This gate does not launch Docker, run generated code, verify container image
provenance, or claim that a Git worktree is an OS sandbox. The existing
`core.sandbox_runtime.build_docker_argv` builds a Docker argument vector but
does not launch or supervise a container. A production runtime still needs
container lifecycle supervision, image digest pinning, output streaming caps,
timeout enforcement, cancellation, daemon/socket isolation, and cleanup. Do
not execute untrusted generated code until that runtime is implemented and
independently reviewed.

## Example

```python
from automation.evolution_execution_gate import EvolutionExecutionGate
from core.sandbox_policy import SandboxPolicy

gate = EvolutionExecutionGate(
    workspace_root="/srv/yuki/worktrees",
    approval_verifier=approval_service.verify_exact_plan,
)
spec = gate.prepare(
    plan_id="plan-123",
    request_id="change-123",
    plan_digest="<64-character SHA-256 digest>",
    actor_id=authenticated_actor_id,
    image="registry.example/yuki-runner@sha256:<pinned-digest>",
    workspace="/srv/yuki/worktrees/change-123",
    command=("python", "-m", "unittest"),
    policy=SandboxPolicy(
        network_mode="none",
        capabilities=frozenset({"read_workspace", "write_workspace"}),
    ),
)
# Pass spec only to a separately audited sandbox supervisor.
```

## Tests

```bash
python -m unittest tests.test_evolution_execution_gate
```
