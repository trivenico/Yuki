# Portable release anti-rollback state

`yuki_trust.anti_rollback.advance_release_state` is the durable state primitive for host-enforced portable release monotonicity.

The host installer must provision an absolute state-file path in a persistent,
host-owned directory outside removable media. The state file is created with
restrictive permissions, a sidecar lock serializes concurrent launch attempts,
and replacement is atomic with file and parent-directory synchronization on
POSIX systems.

The state binds the highest accepted non-negative release sequence to the
SHA-256 digest of the exact signed manifest bytes. Older sequences are refused.
Reusing a sequence for different manifest bytes is also refused (equivocation).
Replaying the exact same release is idempotent. Corrupt or unreadable state
fails closed; it is never silently reset.

Important limitations:

- The signed manifest now requires a non-negative `release_sequence`. Both manifest generators and the release-candidate builder require it explicitly.
- The trusted bootstrap advances this state only after the staged copy passes signature and exact-inventory verification, immediately before starting the signed interpreter.
- The host-installed launch policy pins an absolute `anti_rollback_state` path outside removable media. Parent directories must already exist and pass host ownership/permission checks.
- State deletion by a privileged attacker or disk rollback cannot be prevented
  by a local file alone. Stronger guarantees require OS-backed protected
  storage, TPM/secure-enclave monotonic counters, or server-side release
  authorization.
- Windows ACL provisioning is the installer's responsibility. The Windows file lock is implemented, but clean-machine ACL and concurrent-process tests remain required.
