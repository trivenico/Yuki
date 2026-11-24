# Audit 097 — Agent Filesystem Security & Workspace Isolation

**Repository:** trivenico/Yuki  
**Base inspected:** main  
**Scope:** workspace path validation, knowledge indexing, file type/size boundaries, graph export, sandbox workspace mounts.

## Executive summary

The reviewed knowledge-indexing boundary had a time-of-check/time-of-use weakness. It resolved a candidate path and checked workspace containment, then separately called stat/read operations by pathname. A workspace writer able to replace a validated path component between those operations could redirect the later open. The resolved-path symlink check does not make the later pathname open race-free.

A focused fix is submitted in implementation PR #1168. It uses POSIX descriptor-relative traversal with no-follow opens for every path component, verifies the final descriptor is a regular file, and enforces the byte limit while reading. It fails closed where the required primitives are unavailable.

## Reviewed source

- knowledge/indexer.py
- knowledge/knowledge_graph.py
- core/sqlite_file_security.py
- core/sandbox_policy.py
- core/sandbox_runtime.py
- core/coding_task_runner.py
- automation/task_runtime.py
- tests/test_sqlite_file_security.py

## Findings

### P1 — Knowledge indexer pathname TOCTOU

The previous indexer resolved the candidate, checked containment, then separately called is_file/stat/read_text on the path. Path resolution followed symlinks at validation time, but subsequent path-based operations reopened by name. A concurrently mutable workspace could change path components between validation and reading.

**Remediation submitted:** descriptor-relative component traversal from an opened workspace directory; O_NOFOLLOW on each directory and final file; fstat regular-file validation; size check both before and during bounded read; fail closed without required POSIX primitives.

Regression tests cover regular in-workspace reads, symlinked file, symlinked parent directory, traversal, non-regular file, and oversized content.

### P1/P2 — Workspace boundary is not a universal agent filesystem boundary

The inspected sandbox policy and Docker argv builder describe workspace mount permissions, but those controls only apply when execution actually goes through the guarded sandbox runner. They do not establish a repository-wide filesystem policy for every skill, plugin, browser worker, or host-side service.

**Recommendation:** introduce a single workspace broker that issues per-task directory handles or opaque workspace IDs, applies owner/tenant/agent ACLs, and exposes explicit read/write/export operations. Avoid passing arbitrary host paths to agents.

### P1 — Knowledge memory lacks durable source ownership

Indexed chunks are persisted as ordinary lesson memories. The reviewed indexer does not persist a durable source manifest, content fingerprint, owner/tenant namespace, or source-to-chunk deletion relationship. Updating or deleting a source therefore cannot reliably reconcile every derived memory.

**Recommendation:** add a transactional index manifest linking principal, workspace, canonical source identity, content digest, index version, and generated memory IDs. Make reindex/delete operate from that manifest.

### P2 — Knowledge graph export destination is caller-controlled

KnowledgeGraph.export_obsidian accepts a caller-supplied directory, resolves it, then writes generated files by pathname. The class does not enforce an application-owned export root, per-principal destination authorization, or a manifest for generated copies. Existing symlinked output filenames and concurrent destination changes deserve explicit handling.

**Recommendation:** route exports through the workspace broker; require an authorized destination capability; write with no-follow/exclusive temporary files and atomic replacement; record export provenance and cleanup ownership.

### P2 — SQLite file security is defense in depth, not workspace isolation

The SQLite path helper protects the final database file against symlink/hard-link use and applies owner-only permissions on POSIX. Parent directories are resolved and created, but the helper is not a general-purpose secure workspace resolver and does not isolate processes sharing the same OS identity.

### P1 — Container workspace mounts are not host filesystem isolation by themselves

Sandbox runtime declarations and Docker argv restrictions are useful defense in depth, but the Docker daemon remains a privileged trust boundary. A container mount policy does not establish host-side attestation or prevent access through a compromised daemon/host. Per-agent isolation requires a hardened rootless worker or dedicated VM boundary, with explicit mounts and no ambient credentials.

## Implementation status

- PR #1168 contains the knowledge indexer fix and regression tests.
- Audit documentation is submitted separately in PR #1169.
- Tests were added but not executed in this work session.
- No runtime deployment, filesystem race harness, cross-agent integration test, or container escape test was performed.
- No PR has been merged.

## Recommended architecture

1. Workspace broker issues principal- and task-scoped workspace handles.
2. Agents never receive arbitrary host paths or host filesystem credentials.
3. Read/write/export operations are mediated by explicit capabilities and audited.
4. Each task receives an immutable input snapshot and separate output directory.
5. Index manifests preserve source provenance and enable deterministic reindex/delete.
6. High-risk code runs in rootless isolated workers or disposable VMs, not the control-plane process.
7. Cleanup is verified and reconciled after worker crashes.

## Conclusion

The indexer's path validation/read race is a concrete defect and has a narrow fix submitted. The larger unresolved issue is that Yuki does not yet demonstrate one enforceable workspace-security boundary shared by all agent execution paths. That should become a control-plane/workspace-broker milestone rather than a collection of isolated path checks.
