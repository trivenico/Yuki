# Linux native runner bootstrap design

Status: proposed architecture; not implemented or approved for execution.

## Decision

Do not extend `evaluation/linux_blocked_child.py` into a production launcher.
Its child path runs Python after `fork()`. A check of
`threading.active_count()` is not a sufficient proof that the interpreter,
native extensions, allocator, logging, or runtime libraries have no inherited
locked state. The helper remains an experimental, non-executing lifecycle
probe and must never be used to execute untrusted candidate code.

The production direction is a small, separately built native bootstrap
executable (initially C, with a deliberately narrow syscall/API surface).
The trusted Python supervisor should use a supported spawn/exec mechanism to
start this executable with an explicit descriptor allowlist. The native
process must not evaluate candidate content until every parent and child gate
has been verified.

## Trust boundaries

- Python supervisor: policy validation, artifact authorization, cgroup
  ownership, orchestration, audit events, and bounded cleanup.
- Native bootstrap: process-local namespace transitions, credential and
  capability changes, mount setup, seccomp installation, and final exec.
- Kernel: enforcement of namespaces, cgroups, descriptor access, mount flags,
  no-new-privileges, capabilities, and seccomp.
- Candidate: untrusted; receives only the final artifact interface and
  explicitly approved runtime descriptors.

The native helper is trusted computing base code. Keep it small, memory-safe
where practical, dependency-light, versioned, reproducibly built, and reviewed
independently. No shell command strings, dynamic library search paths, or
candidate-controlled environment values may influence bootstrap behavior.

## Required protocol

Use a versioned, length-bounded local `SOCK_SEQPACKET` control protocol. Every
message includes a protocol version, message type, and attempt nonce. Reject
unknown versions, unknown message types, extra/truncated packets, duplicate
transitions, and unexpected ancillary file descriptors. Authenticate the
peer with kernel credentials where available; bind all parent operations to
the exact child process identity (pidfd plus procfs identity where needed).

1. **Stage immutable artifact.** Supervisor creates and verifies a sealed
   memfd, hashes it, and passes only its descriptor. Native helper verifies
   descriptor type, required seals, and expected digest. Never reopen a
   caller-controlled path.
2. **Spawn trusted helper.** Start the native executable with close-on-exec
   defaults and an explicit allowlist. Do not execute Python code in a
   post-fork child. The helper closes every descriptor outside its allowlist
   before it reports readiness.
3. **Initial gate.** Helper sends `BOOTSTRAP_READY` and blocks. Supervisor pins
   identity, checks the descriptor set, creates/configures the attempt cgroup,
   moves the helper into it, and verifies actual membership and resource
   controller read-back. Ancestor limits and delegated-root ownership must
   also be checked.
4. **Create user namespace.** On parent authorization, helper performs the
   user-namespace transition and reports `USERNS_READY` while remaining
   blocked. Supervisor writes `setgroups=deny` before `gid_map`, then writes
   the explicitly authorized UID/GID maps through a pinned procfs process
   directory. Re-read maps and verify namespace identity before continuing.
5. **Create remaining namespaces.** Create the required mount, network, IPC,
   UTS, and PID isolation with explicit flags. PID namespace semantics require
   special handling: `unshare(CLONE_NEWPID)` affects subsequently created
   children, not the calling process. The helper must fork/exec a dedicated
   payload process inside the new PID namespace or use a reviewed clone-based
   design; do not claim the helper itself entered the new PID namespace.
6. **Prepare filesystem.** Construct a minimal mount tree in the private
   mount namespace. Make propagation recursively private before changing
   mounts. Mount only approved read-only runtime and artifact inputs; provide
   bounded writable scratch and minimal `/proc` only if required. Use
   `pivot_root` or an equivalently reviewed root transition, detach the old
   root, and verify mount flags and mount identities. No host root or ambient
   host paths.
7. **Drop authority.** Set supplementary groups deliberately, set real,
   effective, and saved GID/UID, clear ambient/permitted/effective/inheritable
   capabilities as appropriate, verify capability sets and securebits, set
   `PR_SET_NO_NEW_PRIVS`, and verify all four UID/GID status fields. Do not
   rely on one API call as proof of privilege removal.
8. **Install syscall policy.** Install a reviewed architecture-specific seccomp
   filter after all required setup operations and before candidate exec.
   Validate architecture and syscall numbers; default-deny or tightly
   allowlist syscalls. Seccomp is defense in depth, not a replacement for
   namespaces, cgroups, filesystem restrictions, or descriptor hygiene.
9. **Final parent gate.** Helper reports a complete kernel-state snapshot.
   Supervisor independently checks namespace inode separation, credentials,
   capabilities, no-new-privileges, mount state, cgroup membership, artifact
   digest/seals, and descriptor allowlist. Revalidate process identity and
   artifact binding immediately before authorization.
10. **Exec candidate.** Only after every gate succeeds may the helper execute
    the fixed trusted runtime with the immutable artifact descriptor. The
    candidate must not receive the control channel, supervisor descriptors,
    host credentials, network access, or arbitrary environment variables.
11. **Cleanup.** On any failure, timeout, cancellation, or protocol violation,
    terminate through the pinned process handle, verify exit/reap, verify the
    cgroup is empty, remove the cgroup, and close owned descriptors. Never
    report cleanup success without independent verification. Preserve handles
    and emit a critical failure if cleanup cannot be proven.

## Implementation constraints

- No candidate bytes execute during any bootstrap phase.
- Every phase is a monotonic state transition; rollback is termination, not
  an attempt to reverse partially applied namespace or credential changes.
- Every syscall failure is fatal. No fallback to weaker isolation.
- All protocol reads/writes and waits are bounded and handle EINTR, short I/O,
  EOF, and malformed packets explicitly.
- Validate kernel feature availability before starting an attempt; fail closed
  when required namespaces, cgroup v2 controllers, pidfds, seccomp, or mount
  operations are unavailable.
- Do not trust `/proc` snapshots alone. Pin process identity, use directory
  descriptors for procfs operations, and account for the fact that snapshots
  are point-in-time observations.
- Build the native helper in a reproducible CI job with compiler warnings as
  errors, static analysis, dependency/SBOM capture, and signed build
  provenance. Test in disposable Linux VMs across supported kernels.
- Unit tests must cover protocol parsing, invalid state transitions, FD
  allowlists, mapping order, namespace failures, capability dropping,
  seccomp denial, timeout/cancellation, and cleanup failure. Integration
  tests must verify actual kernel state and prove a malicious test payload
  cannot access host files, network, credentials, or forbidden syscalls.
- Keep the current Python fork helper explicitly experimental until the
  native implementation passes independent security review and CI.

## Acceptance gates before production

- Native source and build configuration reviewed.
- Reproducible, pinned toolchain and dependency inventory recorded.
- CI tests pass on the minimum and current supported Linux kernels.
- Disposable-VM integration tests prove every isolation property from kernel
  read-back, including PID namespace behavior and mount propagation.
- Negative tests prove no candidate exec occurs when any gate fails.
- Independent security review signs off on the TCB and threat model.
- Operator kill, timeout, host restart, and cgroup cleanup paths are tested.
- No production feature flag can enable the runner before all gates pass.

Until all gates are met, the runner is **not production-ready** and must remain
disabled for untrusted execution.

## Protocol reference model

The Python module `evaluation/native_runner_protocol.py` is a reference
specification and test oracle for the versioned header and monotonic message
sequence. Its tests validate malformed packets, nonce mismatch, unexpected
ancillary descriptors, role direction, duplicate/out-of-order transitions,
and terminal abort/authorization states. It is not the native transport,
does not authenticate a real peer, does not receive kernel credentials, and
cannot be used to launch or authorize candidate code. The native implementation
must independently implement this wire contract and pass cross-implementation
tests before integration.

## Native protocol implementation status

The C implementation is in `evaluation/native_runner_protocol.[ch]`. It
provides fixed-header decoding, nonce comparison, role-aware monotonic state
transitions, and rejection of ancillary descriptors at the API boundary.
`tests/native_runner_protocol_test.c` is a standalone C conformance smoke test.

The wire header is exactly 24 bytes: four-byte ASCII magic `YUKI`, one-byte
version, one-byte message type, two-byte big-endian payload length (currently
required to be zero), and a 16-byte attempt nonce. The implementation is
transport-agnostic. Callers must use a correctly configured `SOCK_SEQPACKET`
socket, detect packet truncation, inspect and reject all unexpected control
messages/file descriptors, authenticate the peer using kernel credentials,
and enforce bounded I/O and deadlines.

Compile and run the standalone C test in a disposable Linux build environment:

```sh
cc -std=c11 -Wall -Wextra -Werror -pedantic \
  -I evaluation evaluation/native_runner_protocol.c \
  tests/native_runner_protocol_test.c -o /tmp/yuki-native-protocol-test
/tmp/yuki-native-protocol-test
```

The test is authored but has not been run here. Python/C golden-vector
comparison and fuzzing remain required. The C parser/state machine is not a
launcher and performs no namespace, cgroup, mount, credential, seccomp, or
exec operations. It must not be wired to candidate execution.

## Linux transport implementation status

The Linux-only transport adapter is in
`evaluation/linux_runner_transport.[ch]`. It requires Unix
`SOCK_SEQPACKET`, uses bounded `poll` waits and nonblocking packet I/O,
checks `SO_PEERCRED` against an expected PID/UID/GID on receive, and uses
`recvmsg` with close-on-exec descriptor handling. Any ancillary control
message, received descriptor, truncated packet, or truncated control buffer
is rejected; received SCM_RIGHTS descriptors visible to the process are
closed before returning failure. The caller retains socket ownership.

The adapter is a low-level boundary, not a complete authenticated channel:
the expected peer identity must come from a trusted, independently pinned
process handle; `SO_PEERCRED` and the attempt nonce do not prove executable
integrity or authorize the peer. Callers must also ensure socket creation,
pairing, ownership, lifecycle, and state access are serialized. The adapter
does not create the socket, spawn or pin the helper, configure namespaces,
apply cgroups, install seccomp, or execute candidate code.

Linux transport tests cover a successful local packet exchange, socket-type
rejection, peer-credential mismatch, and SCM_RIGHTS rejection. CI compiles
the adapter and test with strict compiler diagnostics. These tests are
authored for CI; they have not been run locally as part of this change.
