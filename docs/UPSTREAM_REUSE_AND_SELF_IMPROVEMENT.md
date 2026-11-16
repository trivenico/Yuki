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


## Review: OpenWorker and MUE-X (2026-11-16)

### OpenWorker

- Upstream: https://github.com/andrewyng/openworker
- Reviewed source snapshot: ecb445490fc054d52b8ea610d97ba947f57033d2
- License: MIT; preserve Andrew Ng's copyright and MIT notice if source is copied.
- Useful design patterns: non-overridable human-only hard floors; durable approval provenance for every tool action; reviewer uncertainty and repeated-denial circuit breakers; per-agent execution isolation; no model/API credentials inside the sandbox; explicit network allowlists; refusal when configured isolation cannot be enforced.
- Yuki adoption: retain PermissionGate, approval journal, guarded tool runtime, and network firewall as canonical controls. Prioritize a sandbox adapter for shell/file tools and report enforcement mode in tool results. Keep hard floors independent of any reviewer or auto-approval feature.
- Do not copy its full desktop shell, engine, or reviewer implementation into Yuki; that would create competing runtimes and permission systems. Review transitive dependency licenses separately before importing code.

### MUE-X

- Upstream: https://github.com/KorroAi/mue-x
- Reviewed source snapshot: c7038448abb340b23d03af0fd095cefbfbbcadd9
- License: MIT; preserve Korrocorp's copyright and MIT notice if source is copied.
- Useful design patterns: AST-level source analysis; explicit backups; syntax/import/test gates; rollback on failed validation; mutation outcome tracking; searchable persistent error patterns and task/skill specialization.
- Security findings: the inspected SecurityGuard uses substring matching for shell commands, allows almost all unmatched commands, and treats audit-log write failures as non-fatal. Its path protection is not a substitute for a filesystem sandbox or Yuki authorization checks. Do not transplant these as a security boundary.
- Yuki adoption: extend core/self_improvement.py's proposal-only boundary with isolated worktrees/branches, AST/syntax checks, targeted tests, dependency and secret scans, human approval, staged rollout, health checks, and rollback. Record outcomes as structured evaluation data. Never let a model apply its own patch to the live process or alter its governing approval/firewall policy.
- Do not import MUE-X's autonomous self-rewrite loop or broad shell/network permissions. Claims of continuous self-evolution are not independent evidence of safety or production reliability.

### Integration order for Yuki

1. Finish shared firewall composition in every real provider/runtime constructor and verify transport identity.
2. Close the execution-sandbox gap for shell and file tools; credentials must remain outside the sandbox.
3. Make approval provenance durable and inspectable at the action journal/runtime boundary.
4. Add supervised code-improvement evaluation only after execution, approval, and rollback boundaries are tested.
5. Keep desktop UX, 100 specialist agent roles, and autonomous mutation expansion behind these core readiness gates.
