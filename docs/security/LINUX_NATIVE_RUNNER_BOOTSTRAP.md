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

## Inert native helper milestone

The first native helper prototype is
`evaluation/linux_runner_helper.c`. It accepts a control descriptor, the
supervisor PID/UID/GID, and a 128-bit nonce as strictly parsed arguments.
The control descriptor is fixed at fd 3. Before protocol activity, the helper
uses Linux `close_range` to close descriptors 0-2 and every descriptor above
3, then marks fd 3 close-on-exec. If the kernel cannot enforce this exact
allowlist, startup fails closed. The parent-side adapter described below now arranges fd 3 and pins the initial executable image.

Before descriptor setup, the helper arms Linux `PR_SET_PDEATHSIG` with
`SIGKILL` and immediately verifies `getppid()` against the expected
supervisor PID. This ordering closes the parent-death race: if the supervisor
dies before the signal is armed, the parent-PID check fails; if it dies after
arming, the kernel delivers the signal. The integration test also kills a
separate supervisor process and verifies the helper pidfd becomes readable.

The helper enables per-message credentials on its receiving endpoint, sends
`BOOTSTRAP_READY`, and waits with a finite timeout for the supervisor.
This milestone accepts only `ABORT`. If any not-yet-implemented authorization
message arrives, it sends `ABORT` and exits without performing namespace
changes or executing a candidate. It is an inert protocol endpoint, not a
sandbox or a candidate runner.

`tests/linux_runner_helper_test.c` exercises a real fork/exec handshake in
Linux CI. The parent opens a pidfd for the child, authenticates the helper's
per-message kernel credentials, inspects the blocked helper's procfs descriptor
set to require exactly fd 3, then sends `ABORT` and verifies clean exit and
pidfd readability. The test does not establish executable integrity or
replace a production supervisor's independent identity and lifecycle checks.

The parent-death signal is a child-side fail-safe, not a replacement for
trusted parent lifecycle management. Remaining work includes integrating the spawn primitive into the trusted
supervisor, executable/build provenance and immutability policy, cgroup placement, namespace/mount/capability/seccomp setup,
and independent review. No candidate execution is implemented or permitted.

## Parent-side native spawn adapter

The Linux-only parent adapter is implemented in
`evaluation/linux_runner_spawn.[ch]`. It accepts an absolute helper path,
opens and pins the ELF executable, requires it to be owned by root or the
effective user, with no owner/group/other write bits, no setuid/setgid
mode bits, and no `security.capability` file capability xattr (or a
filesystem response that prevents checking it), and uses `posix_spawn`
rather than running C or Python runtime code in a post-fork child. This
ownership/mode check prevents an untrusted or writable inode from being
treated as a trusted helper; deployments must still protect the helper and
its dynamic dependency closure against replacement. It creates a close-on-exec
`SOCK_SEQPACKET` pair, enables `SO_PASSCRED` on the supervisor receive
endpoint before spawn, maps the control endpoint to fd 3, and maps the pinned
executable descriptor to fd 4 only for the initial exec. The spawn file
actions close descriptors 5 and above. The helper then closes fd 4 and every
other descriptor except fd 3 before reporting readiness. The environment is
a fixed minimal allowlist; no shell is invoked.

The adapter opens a pidfd, authenticates the helper's per-message kernel
credentials, waits for `BOOTSTRAP_READY`, and compares the kernel's
`/proc/<pid>/exe` device/inode with the pinned executable descriptor. Using
`/proc/self/fd/4` for exec binds the initial image to the already-open inode
rather than resolving a mutable pathname a second time. This depends on procfs
being available and is Linux-specific; failure is fatal. An open descriptor
does not make file contents immutable: a process with write access to that
inode could still modify it while the kernel loads the image. Production must
therefore use a root-owned, non-writable build location and add verified
build provenance or fs-verity/signature enforcement before treating the
executable as trusted. The returned handle
contains the PID, pidfd, control socket, peer UID/GID, and protocol state.
The abort path sends the protocol abort, reaps the exact child, and closes
owned descriptors.

This is a spawn primitive, not a production supervisor. The helper currently
supports abort only. The adapter does not create or verify cgroups, namespaces,
mounts, capabilities, seccomp, immutable artifact staging, or candidate
execution. Its integration test checks the helper's post-bootstrap descriptor
allowlist and pidfd lifecycle. CI coverage is authored but must pass before
this milestone can be considered verified.

The spawn handle now records Linux `/proc/<pid>/stat` start-time ticks alongside the pidfd. The adapter checks that the pidfd is still live around the procfs executable identity check, and cleanup revalidates the same start-time identity before reaping. Cancellation has a caller-supplied bounded timeout: it first sends protocol `ABORT`, waits on the pidfd, then escalates with `pidfd_send_signal(SIGKILL)` and waits again. If pidfd creation itself fails, the direct child PID remains unreaped and cleanup uses bounded `waitpid(WNOHANG)` polling after SIGKILL. If termination or reap cannot be verified, the returned child structure retains its PID and any available descriptors so the caller can retry cleanup. CI tests exercise both graceful abort and escalation against a stopped helper.

## Linux transport implementation status

The Linux-only transport adapter is in
`evaluation/linux_runner_transport.[ch]`. It requires Unix
`SOCK_SEQPACKET`, uses bounded `poll` waits and nonblocking packet I/O; a zero-millisecond timeout performs an immediate readiness check.
Callers must invoke `yuki_linux_transport_enable_credentials` on each
receiving endpoint before the peer can send; receive verifies that
`SO_PASSCRED` was enabled in advance and checks kernel-supplied per-message
`SCM_CREDENTIALS` against the expected PID/UID/GID. It uses `recvmsg` with
close-on-exec descriptor handling. Any ancillary control
message, received descriptor, truncated packet, or truncated control buffer
is rejected; received SCM_RIGHTS descriptors visible to the process are
closed before returning failure. The caller retains socket ownership.

The adapter is a low-level boundary, not a complete authenticated channel:
the expected peer identity must come from a trusted, independently pinned
process handle. Per-message kernel credentials identify the sending process
for each packet; the attempt nonce does not prove executable integrity or
authorize the peer. `SO_PEERCRED` remains available as a connection-level
check, but is not used as the per-message identity because a socketpair made
before fork can retain credentials from the process that created the pair. Callers must also ensure socket creation,
pairing, ownership, lifecycle, and state access are serialized. The adapter
does not create the socket, spawn or pin the helper, configure namespaces,
apply cgroups, install seccomp, or execute candidate code.

Linux transport tests cover a successful zero-timeout packet send, an
empty-queue zero-timeout receive, socket-type rejection, connection- and
per-message peer-credential mismatch, and SCM_RIGHTS rejection. CI compiles
the adapter and test with strict compiler diagnostics. These tests are
authored for CI; they have not been run locally as part of this change.


## Opaque native lifecycle ABI

The C wrapper in `evaluation/linux_runner_abi.[ch]` exposes an opaque handle
with only create, spawn, PID inspection, abort/reap, and destroy operations.
It deliberately does not expose the internal C structure layout to Python.
The Python binding in `evaluation/linux_runner_ctypes.py` requires an explicit
shared-library path and is Linux-only. A failed abort preserves the handle so
the caller can retry; destroying a handle with a live child or owned descriptor
is rejected as busy.

The ABI is lifecycle plumbing for the inert helper only. It does not implement
or authorize cgroup placement, namespace transitions, mount isolation,
capability dropping, seccomp, artifact binding, or candidate execution. The
shared library must be built from reviewed sources in a trusted build pipeline;
loading an arbitrary library path is not safe. The ctypes wrapper is not
connected to `run_supervised_attempt` and must not be treated as a production
runner adapter. CI compiles the shared object and exercises the C ABI; Python
unit tests cover argument validation and preservation of a handle after an
abort error. These checks are authored, not yet verified by a reported CI run.


### Python binding library-loading contract

The ctypes binding opens the configured shared object with no-follow semantics,
checks that it is a regular ELF file owned by root or the effective user, and
rejects any write permission bits. It then loads the already-open inode through
`/proc/self/fd`, avoiding a second lookup of the configured pathname. This
requires procfs and fails closed if the file cannot be pinned or inspected.
Helper paths must be absolute and NUL-free; timeout values are range-checked
before crossing the C integer ABI. Context-manager exit attempts bounded helper
abort before destroying the opaque handle; if abort fails, the handle is
retained for explicit retry.

These checks do not establish the trustworthiness of the shared object's
dynamic dependencies, ELF ABI, compiler provenance, RPATH/RUNPATH, or runtime
loader configuration. The shared object and its dependency closure must come
from a trusted, reproducible build and protected installation. Do not load a
library from a user-writable or otherwise untrusted location. The wrapper is
still lifecycle plumbing only and is not a `RunnerSupervisorAdapter`.

The C ABI also exports `yuki_linux_runner_abi_version()`; the Python wrapper
requires version 1 before allocating a handle. Bump this value when exported
signatures or ownership semantics change. The wrapper requires Linux
`O_NOFOLLOW` and `O_CLOEXEC` support rather than silently weakening the
library-open checks. Calls on a single native handle must be serialized by its
owner; this ABI does not provide internal concurrent-call synchronization.

### Native helper linkage audit in CI

The native-runner quality workflow now performs an additional static-link
audit build of the inert helper. It compiles with `-static`, checks the ELF
program headers for absence of a `PT_INTERP` interpreter, checks the dynamic
section for absence of `DT_NEEDED` entries, and records the artifact's SHA-256
in the job log. This makes the audited helper build independent of a runtime
dynamic loader and shared-library dependency resolution on the target host.

This is a CI audit artifact, not a signed release artifact or a reproducible
build attestation. The workflow still uses the runner-provided compiler and
system static libraries; it does not pin or attest the compiler, libc archive,
build image, or source-to-binary correspondence. The normal spawn integration
test continues to use its ordinary helper build. No deployment or production
execution is authorized by this check. A release pipeline still needs pinned
toolchain inputs, provenance/SBOM capture, independent artifact verification,
and protected installation paths.

### Spawn failure and recovery-handle ownership

The native spawn adapter's error contract distinguishes pre-spawn failures from
post-spawn cleanup failures. A pre-spawn failure returns an empty child handle.
If startup fails after a helper exists, the adapter attempts bounded abort and
reap. When that cleanup succeeds, it returns the original startup error with
an empty handle. When cleanup cannot be verified, it returns failure while
preserving the PID, control descriptor, and pidfd when available in the caller's
child structure. The caller must treat that structure as live owned state and
retry bounded abort/reap; it must not overwrite or discard it. The opaque C ABI
preserves the same state inside its handle, so a failed spawn can still be
followed by PID inspection and abort. This prevents cleanup errors from being
silently converted into unreachable child processes or leaked descriptors.
