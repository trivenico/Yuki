# Upstream reuse and fork plan

## Decision

Yuki can reuse upstream projects, but do not copy whole runtimes into the same process. Keep Yuki's permission gate and registered-skill boundary as the canonical execution policy. Integrate one capability at a time behind an adapter, with tests and a recorded source/license manifest.

## Candidate projects

- Hermes Agent: https://github.com/NousResearch/hermes-agent
- OpenClaw: https://github.com/openclaw/openclaw
- OpenJarvis: the name is ambiguous across public repositories. Verify the intended canonical upstream before importing code; do not assume similarly named repositories are the same project.

## Integration sequence

1. Record upstream repository, immutable commit SHA, exact file paths, license and copyright notices.
2. Review security model, dependency footprint, telemetry/network behavior, and transitive licenses.
3. Port a narrowly scoped adapter into Yuki rather than importing a second main loop or permission system.
4. Add contract tests proving all actions still pass through `PermissionGate`, registry allow-lists, timeouts, and audit events.
5. Run unit, dependency, secret, static-analysis, and supported-platform CI.
6. Keep the upstream source isolated until the candidate is reviewed; never auto-run upstream install scripts or generated code.
7. Require human review for changes to permissions, tool execution, credentials, dependency manifests, CI workflows, deployment, and self-modifying core modules.

## Forking status

The currently connected GitHub tools can read and modify files and branches in repositories the user can access, but do not expose a repository-fork creation operation. Therefore no upstream fork is claimed or fabricated here. Forking can be done through GitHub's Fork UI/API by the account owner; Yuki integration can then proceed with reviewed, pinned source snapshots.

## Current Yuki self-improvement boundary

`core/self_improvement.py` accepts a bounded text proposal, rejects unsafe paths, duplicate names, binary content and oversized proposals, and marks sensitive core/workflow/dependency changes as requiring human approval. It does not write files, run generated code, create commits, or deploy. This is intentional: proposing and validating a change is safer than allowing a model to directly mutate its own execution policy.

## Exit criteria

- A clean, reproducible test run on Ubuntu, Windows, and macOS.
- Explicit license/provenance records for every imported component.
- Regression tests demonstrating denial when approval is missing, malformed, or raises.
- Self-improvement changes run only in an isolated worktree/branch and cannot self-approve protected changes.
- Rollback and recovery procedures tested before release.
