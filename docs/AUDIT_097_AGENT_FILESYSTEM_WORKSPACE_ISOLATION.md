# Audit 097 — Agent Filesystem Security, Workspace Isolation, and Sensitive Data Protection

Repository: trivenico/Yuki  
Base: main  
Scope: authorized workspace read tools, approved workspace edits, file listing, knowledge indexing, sandbox workspace mounts, portable workspace configuration.  
Outcome: one implementation fix submitted; additional cross-boundary findings documented.

## Executive summary

Yuki has explicit workspace-oriented controls in several paths: authorized read tools require a trusted principal and workspace:read permission; file paths are constrained to a configured root; secret-like path components and common credential directories are blocked; approved edits require a path allow-list, expected content digest, explicit approval, and audit; core sandbox mounts the workspace read-only unless write_workspace is granted.

A concrete TOCTOU risk exists in AuthorizedReadOnlyTools: path resolution and containment validation occur before a separate Path.read_text() open. A concurrent workspace writer can replace a validated file or parent directory with a symlink between those operations. PR #1168 changes the POSIX read path to descriptor-relative traversal anchored at the workspace root, uses O_NOFOLLOW for each directory and the final file, checks the opened descriptor with fstat, and enforces the read bound while reading. Regression tests were added but not executed.

The change hardens only AuthorizedReadOnlyTools.workspace.read. The indexer, approved edit path, file listing, sandbox mount preparation, and legacy sandbox need separate race-resistant handling. Cross-agent isolation also depends on composition assigning distinct protected workspace roots; the reviewed read-tool principal is actor-scoped but the tool instance itself owns one configured root.

## Files inspected

- automation/authorized_read_tools.py
- automation/approved_workspace_changes.py
- skills/file_skill.py
- knowledge/indexer.py
- core/sandbox_policy.py
- core/sandbox_runtime.py
- core/coding_task_runner.py
- hackback/sandbox.py
- core/portable_paths.py
- core/gate_first_sandbox_executor.py
- tests/test_authorized_read_tools.py
- tests/test_approved_workspace_changes.py
- tests/test_portable_paths.py
- tests/test_sandbox_runtime.py

## Positive controls observed

1. AuthorizedReadOnlyTools has an explicit registry limited to workspace.list, workspace.read, and workspace.search; no write, shell, process, network, or Git mutation tool.
2. Read tools require a trusted principal callback and workspace:read permission, use bounded JSON arguments, bounded file sizes/search results, and fail closed if audit is unavailable.
3. Path traversal and absolute paths are rejected; resolved paths must remain beneath the configured workspace root.
4. Common secret directories and secret-like path components are excluded, including .env variants, .ssh, .aws, .azure, .config, token/password/credential/private-key names.
5. Workspace reads are labelled untrusted content for downstream agent use.
6. ApprovedWorkspaceChanges requires a trusted principal, workspace:write permission, explicit allowed paths, expected SHA-256 digest, digest-bound approval, audit preflight, bounded changes, and rollback attempt.
7. Approved edit paths reject parent traversal, blocked secret paths, and symlinks at each currently existing component.
8. Core sandbox defaults to a read-only workspace mount and only enables write mount with explicit write_workspace capability.
9. KnowledgeIndexer checks resolved source containment, supported extension, file size, and content bounds.
10. Portable workspace paths resolve relative workspaces under YUKI_HOME or the data root.

## Findings

### P1 — Authorized workspace reads had a path-validation/open TOCTOU window

Before PR #1168, _safe_path resolved the path and checked that it remained beneath the configured root, but _read_file then performed separate path-based stat and read_text calls. A concurrent process able to mutate the workspace could replace a path component after validation and before open, potentially redirecting the read outside the intended workspace.

Remediation submitted in PR #1168:
- Use directory descriptors rooted at the configured workspace on POSIX.
- Traverse each intermediate component with O_DIRECTORY and O_NOFOLLOW.
- Open the final component with O_NOFOLLOW.
- Verify the opened descriptor is a regular file using fstat.
- Enforce the byte limit on the opened descriptor's data.
- Add regression coverage for symlinked final and intermediate components.

Tests added but not executed. On platforms without POSIX O_NOFOLLOW and directory descriptor support, the implementation retains a resolved-path fallback; equivalent Windows handle-relative reparse-point protections remain future work. A concurrent adversarial race harness was not run.

### P1 — Workspace isolation is an instance-composition responsibility, not inherent in the read-tool principal

AuthorizedReadOnlyTools is constructed with one workspace root and an actor principal. Its methods authorize the actor to read that tool instance's root, but the class does not itself map an actor, tenant, or agent identity to a unique workspace root.

Risk: if application composition shares one tool instance across agents or owners whose data should be isolated, principal authorization alone does not create per-agent filesystem separation. A trusted gateway must select the correct root and instantiate or scope the tool accordingly.

Required direction:
- Resolve workspace roots from authenticated owner/tenant/agent identity in trusted composition.
- Store workspace ownership metadata and enforce it on every operation.
- Use separate OS identities or isolated worker mounts for high-risk agents.
- Never accept a workspace root directly from model-generated arguments.
- Add cross-owner/cross-agent integration tests against the real composition layer.

### P1 — Knowledge indexing has a separate resolve/stat/read race and incomplete source lifecycle

KnowledgeIndexer resolves a candidate path and checks containment, then separately stats and reads it. The symlink test is applied after resolve, where a resolved path is normally no longer a symlink. The containment check blocks a symlink target outside the root at validation time, but a concurrent path replacement can still change what is read after validation. The resulting memory chunks also do not carry a durable source manifest in the inspected API.

Required direction:
- Reuse a shared descriptor-relative safe-open primitive for indexing.
- Validate file type, size, and content from the same opened descriptor.
- Record source identity/fingerprint and chunk IDs in a durable index manifest.
- Support source update/delete reconciliation and namespace ownership.

### P1 — Approved workspace edits revalidate content but still use path-based replacement

ApprovedWorkspaceChanges rejects symlinks during validation and rechecks file content digest before replacement. However, the resolved target path is retained and later used for path-based reads and os.replace. Parent directory replacement races and file identity changes need stronger directory-descriptor-relative operations; digest equality alone does not bind the directory entry to the same inode.

Required direction:
- Hold trusted directory descriptors for the workspace and target parents.
- Use no-follow openat-style traversal and verify device/inode identity.
- Use descriptor-relative temp creation and atomic replacement where supported.
- Recheck file identity and digest immediately before commit.
- Add concurrent rename/symlink swap tests and rollback tests.

### P1 — Sandbox workspace path is selected by caller and mounted as a host bind mount

The core Docker argv builder accepts an absolute workspace path and mounts that path at /workspace. It rejects a symlink at the final workspace path and resolves it before building the mount, but it does not itself enforce that the path is under a configured per-agent workspace root. The runner relies on its caller and approval callback to supply a safe workspace.

Risk: if a caller supplies an overly broad directory (for example a home directory or application data root), the container receives access to the contents of that directory according to the mount mode. Read-only does not mean least-privilege.

Required direction:
- Derive workspace path from trusted task/owner/agent records.
- Require containment under a dedicated workspace root.
- Exclude secrets, sockets, databases, VCS metadata, and host configuration.
- Prefer immutable source snapshots and a separate output mount.
- Bind mount preparation to a stable directory identity to avoid path replacement between validation and Docker mount.

### P2 — Legacy hackback sandbox always mounts workspace read-write

hackback/sandbox.py mounts its configured workspace read-write for every invocation. This is weaker than core SandboxPolicy's read-only default and explicit write_workspace capability. It also accepts a caller-provided Docker executable string; the separate Audit 096 implementation PR addresses the core runner, not this legacy path.

Required direction:
- Deprecate or route this runner through the core sandbox policy.
- Default to read-only and require explicit approved write capability.
- Require a trusted absolute Docker executable.
- Replace capture_output with bounded streaming output.

### P2 — Secret filename exclusions are heuristic and do not establish secret-content protection

The read tools block common secret directories and filename patterns, but secret material may appear in ordinary files such as source code, logs, configuration with nonstandard names, or generated artifacts. Filename filtering is defense in depth, not a data-loss-prevention boundary.

Required direction:
- Keep credentials outside all agent workspace roots.
- Use secret broker/injection only for explicitly authorized tasks and never mount broad host credential directories.
- Add content-aware redaction for logs and tool outputs where appropriate.
- Classify workspace data and enforce owner/agent ACLs independently of filename.

### P2 — FileSkill and knowledge indexing have different path-handling contracts

FileSkill resolves a requested path and checks containment before listing; KnowledgeIndexer uses a separate implementation; AuthorizedReadOnlyTools has another implementation. Different path semantics create drift and inconsistent hardening.

Required direction:
- Centralize safe workspace path operations (resolve, open, list, write, atomic replace).
- Make trust/provenance and principal namespace explicit.
- Add a shared adversarial path corpus: traversal, absolute paths, nested symlinks, hard links, mount points, Unicode normalization, case folding, and concurrent rename races.

## Implementation status

Implementation PR #1168 hardens only AuthorizedReadOnlyTools.workspace.read on POSIX and adds tests. It does not yet harden KnowledgeIndexer, ApprovedWorkspaceChanges, sandbox mount selection, FileSkill, or hackback/sandbox.py. No merge or deployment has occurred.

## Validation and limitations

Source inspection only. PR #1168 regression tests were added but not executed. No concurrent filesystem race harness, Windows reparse-point test, mount namespace test, cross-agent isolation integration test, or secret-scanning test was run. These findings describe the reviewed source paths, not every deployment composition or OS-specific behavior.
