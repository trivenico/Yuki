# In-process skill trust boundary

**Status:** fail-closed interim policy; this is not an OS sandbox.

## What the current controls do

- `skills.metadata_discovery` parses bounded JSON manifests without importing or executing their Python entrypoints.
- Persistent approval is bound to the manifest content digest.
- Registration accepts a separately instantiated object supplied by a trusted bootstrapper.
- The guarded runtime checks one-shot invocation approval and, where wired through `ApprovedSkillRuntime`, rechecks persistent approval at dispatch.
- Skill argument size and result-data serialization are bounded.

## What they do not do

A Python skill executing in the Yuki process has the process's ambient operating-system authority. A manifest field such as `permissions: ["network"]` is descriptive metadata; it does not mediate sockets, filesystem access, subprocesses, environment variables, credentials, memory, or imported modules. An empty permission list also does not sandbox Python. Risk labels and explicit owner approval are trust decisions, not containment.

The legacy `PluginLoader` imports Python modules. Importing a module executes its top-level code. Its explicit module trust list is a code-trust gate, not a capability sandbox. It must only load code already trusted as part of the application installation.

## Interim enforcement

The in-process approved-skill registration boundary now rejects every candidate with a non-empty permission declaration. Such a skill cannot be wired through `register_approved_skill` or `ApprovedSkillWiringService`; it must wait for a separately isolated execution backend.

Permissionless candidates may still be wired only when a trusted bootstrapper supplies the exact instance. This remains trusted application code and must not be used to load arbitrary third-party or user-supplied Python. An empty manifest permission list must never be presented as proof of restricted OS access.

## Required isolated executor before permissioned skills

A future executor must be a separate OS process/container/VM controlled by deployment-owned code. It must:

1. Receive a signed or otherwise authenticated, short-lived execution envelope binding skill content digest, exact arguments, owner approval, requested capabilities, resource limits, expiry, nonce and fencing generation.
2. Run only an immutable, reviewed artifact in a pinned runtime image; do not import skill code into the owner service process.
3. Enforce filesystem access using an immutable staged workspace and explicit read/write mounts; exclude host home directories, credential stores, sockets and inherited file descriptors.
4. Default to no network. Any future network capability must be enforced by a connection-time egress boundary, not a manifest flag or DNS preflight.
5. Drop privileges and capabilities; require verified namespaces, seccomp, AppArmor/SELinux, cgroups v2, read-only root, process/time/memory/output limits and ephemeral cleanup.
6. Expose only typed, bounded IPC; do not expose arbitrary host RPC, shell, provider credentials or unrestricted tool registries.
7. Bind worker leases and cancellation to the actual execution process/container. Lease loss must fence or terminate the old execution before a replacement can run.
8. Return a bounded structured result and durable execution receipt; reconcile uncertain outcomes without automatic replay.
9. Fail closed if runtime attestation, approval freshness, policy revision, artifact digest, resource enforcement or cleanup cannot be verified.

## Enablement decision

Keep permissioned third-party/user-authored skills disabled. Keep arbitrary code execution and unattended privileged skill execution disabled. Existing in-process built-in skills are trusted application code, not sandboxed agents. Do not claim that the manifest permission list enforces operating-system capabilities until the isolated executor and its adversarial integration tests exist.
