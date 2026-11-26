# Immutable coding-task workspace staging contract

**Status:** design contract only. No staging implementation is enabled; coding-task sandbox execution remains disabled for unattended and privileged work.

## Security objective

The sandbox must not receive a bind mount of the owner's live workspace. It receives only a dedicated, immutable, policy-filtered input snapshot containing files explicitly approved for the task. The snapshot is an input artifact, not a trusted authorization token.

A recursive scan followed by a bind mount of the original workspace is insufficient: files, symlinks, mount points, sockets or directory entries can change between inspection and container startup.

## Required staging pipeline

1. **Resolve a trusted workspace root.** Obtain it from the task's durable reviewed record, not from model-generated execution arguments. Validate the root using no-follow directory descriptors and an explicit owner/profile policy.
2. **Freeze the input identity.** Require a clean reviewed Git tree or an explicitly enumerated file manifest. Record repository identity, commit/tree hash, task patch hash and policy revision. Reject dirty/untracked inputs unless they are explicitly part of the reviewed patch.
3. **Enumerate without following links.** Walk using directory file descriptors and openat-style no-follow operations. Reject symlinks, hard links, sockets, FIFOs, device nodes, mount points, unexpected file types, path traversal, case-folding collisions and names outside the portable path policy. Do not trust Path.resolve() followed by a later path open.
4. **Apply an allowlist.** Include only regular files beneath approved roots. Exclude .git internals unless a narrow immutable source manifest requires selected metadata; exclude credential files, keyrings, cloud configuration, environment files, browser profiles, SQLite databases, sockets, caches, build outputs and host runtime directories by default. Denylist patterns are defense-in-depth, never the primary policy.
5. **Copy from opened descriptors.** Read each source file through the already-validated descriptor, enforce per-file and aggregate byte limits, and write into a newly created private staging directory using exclusive/no-follow creation. Do not preserve ownership, ACLs, xattrs, setuid/setgid bits, capabilities, device metadata or source hard links. Normalize modes and timestamps according to policy.
6. **Verify the staged tree.** Re-open the staging tree without following links, verify every entry type and mode, reject unexpected entries, and calculate a canonical manifest digest over sorted relative paths, file types, normalized modes, sizes and content SHA-256 digests. Bind the digest to the exact task attempt.
7. **Seal and publish atomically.** Keep staging private to the trusted host service. Flush file and directory metadata, then atomically publish the completed snapshot under a random attempt-specific identifier. The sandbox receives a read-only mount of that snapshot only. The owner workspace is never mounted.
8. **Revalidate at launch.** Verify the staging directory identity, ownership, permissions, manifest digest and mount source immediately before container creation. The host launcher must prevent other principals from replacing the staging path. A digest mismatch aborts before launch.
9. **Keep outputs separate.** The sandbox writes only to a distinct bounded scratch/output mount. Treat all output as untrusted. Validate output paths and file types through descriptor-relative no-follow operations before importing selected artifacts into a separate review area. Never copy arbitrary sandbox output over the live workspace automatically.
10. **Clean up by attempt identity.** Retain snapshot metadata and digest as durable evidence. Remove snapshot contents only after the runner is confirmed stopped and the retention policy allows deletion. Cleanup failure is an explicit unresolved state, not success.

## Resource and filesystem limits

The staging service must enforce maximum file count, per-file size, aggregate bytes, path depth, path length, staging duration, scratch size and output size. Reject sparse-file amplification and file growth during copy by checking descriptor metadata before and after reads. A changing source file must cause the snapshot to fail and restart from a new reviewed tree identity; never silently mix versions.

The staging root must be on a filesystem with understood ownership, no-follow and atomic-rename semantics. Unsupported filesystems, network shares with uncertain semantics, Windows reparse points without verified handle-based checks, or unavailable ACL verification must fail closed. POSIX-only validation must not be described as cross-platform support.

## Authorization binding

The durable execution attempt must bind task ID/version and reviewed patch digest; source repository/tree identity; staging manifest digest and staging implementation version; exact normalized sandbox mount plan; runtime image digest and verified runtime attestation digest; argv digest, resource limits and network policy; owner session/revocation generation, policy revision, expiry and unique attempt/fencing generation.

The sandbox worker must not choose a different source path, manifest, mount, image, command or policy after approval.

## Required failure-injection tests

- Symlink, hardlink, socket, FIFO, device node and mount point in source tree.
- Parent directory replaced or source file swapped during enumeration/copy.
- File grows, shrinks or changes while being copied.
- Traversal names, Unicode normalization/case-fold collisions and excessive path depth.
- Secret-bearing paths and Unix sockets are excluded even when nested.
- Per-file, aggregate byte, file-count and duration limits are enforced.
- Staging process crashes before publish: no partial snapshot is mountable.
- Staging path or contents replaced after verification: launch refuses.
- Manifest mismatch or task patch digest mismatch: launch refuses.
- Sandbox output includes symlinks, sockets, traversal paths or oversized files: import refuses.
- Cleanup fails or runner remains alive: snapshot retained and attempt remains unresolved.
- Concurrent attempts receive distinct staging identities and cannot overwrite each other's snapshots.

Tests must exercise real temporary filesystems and descriptor-level races where supported. Platform-specific guarantees require separate POSIX and Windows implementations and tests.

## Enablement decision

Do not implement staging as a convenience wrapper around shutil.copytree, and do not mount the original workspace after a preflight scan. The implementation must use descriptor-relative no-follow operations, a sealed attempt-specific snapshot, launch-time identity verification and a separate output-import path. Until those controls and the trusted runtime verifier exist, keep coding-task sandbox execution disabled for unattended or privileged tasks.
