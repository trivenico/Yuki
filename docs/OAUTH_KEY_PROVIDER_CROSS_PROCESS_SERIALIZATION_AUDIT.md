# OAuth OS Key Provider Cross-Process Lifecycle Serialization Audit

## Scope

Reviewed PR #1346 at head `f6ed16223af10f69e0415ab84266ba89e97d28d3`:

- `core/oauth_os_key_provider.py`
- `scripts/initialize_oauth_keys.py`
- `tests/test_oauth_os_key_provider.py`
- `docs/PHASE174_OS_BACKED_OAUTH_KEY_PROVIDER.md`
- `docs/PHASE175_OAUTH_KEY_VAULT_RUNTIME_WIRING.md`

Source-level review only. No runtime tests or native OS credential-store operations were executed.

## Finding

**The OS key provider does not enforce cross-process serialization for bootstrap or rotation, so concurrent lifecycle operations can publish mutually inconsistent key metadata and make the key store unusable.**

### Evidence

1. The provider's only lock is the class-level `threading.RLock`. It serializes calls among provider instances in one Python process, but it is not shared with other processes.
2. `initialize()` performs multiple independent credential-store writes: initialization marker, AES key record, key index, HMAC key, active-key pointer, and ready marker. The native backend interface offers no multi-record transaction.
3. `rotate()` reads the current index, then independently writes a new key record, a replacement index, and an active pointer. There is no cross-process lock, compare-and-swap, generation check, or post-write consistency verification.
4. The operator-facing `scripts/initialize_oauth_keys.py` constructs a provider and calls `initialize()` directly; it does not acquire a process-shared lock.
5. The phase documentation explicitly tells the host to serialize initialization and rotation across Yuki processes, but neither the provider nor the supplied bootstrap command enforces that requirement.

### Concrete rotation race

Two processes can both read the same initial index. Process A writes key A and index `[old, A]`; process B writes key B and index `[old, B]`; then process A publishes active pointer A after process B has replaced the index. The final state can be index `[old, B]` with active pointer A. `current_key()` rejects this state because the active ID is absent from the index. A similar interleaving can occur during bootstrap, where marker/index/active/HMAC records are independently written.

The exact interleaving depends on native credential-store scheduling, but the provider's API supplies no atomicity or mutual exclusion across processes to prevent it.

### Impact

Concurrent operator invocations, multiple application workers sharing the same OS credential-store namespace, or an overlapping maintenance/rotation process can corrupt the active-key metadata. Once inconsistent, ordinary key retrieval fails closed. OAuth credentials encrypted under historical keys may remain physically present but unavailable to decrypt until an operator reconstructs the correct metadata from backups or other trusted records.

This is an availability and recovery-integrity issue. It is not evidence of key disclosure or a cryptographic bypass.

### Recommended remediation

1. Enforce a process-shared exclusive lock around the entire bootstrap and rotation lifecycle. Use a platform-appropriate lock primitive with explicit ownership, timeout, and stale-owner recovery semantics.
2. Do not rely on a PID file or check-then-create lock without atomic creation and safe stale-lock handling.
3. Add a monotonic generation/version to the lifecycle manifest and require compare-and-swap semantics where the backend supports them.
4. After writes, re-read and validate that the active pointer belongs to the published index and that the referenced key material is retrievable before reporting success.
5. Make the CLI acquire the same lifecycle lock as runtime rotation.
6. Add deterministic interleaving tests with two independent provider instances backed by a shared fake credential store, covering both initialization and rotation. Assert that concurrent calls either serialize successfully or one fails without publishing inconsistent state.

## Severity and confidence

**Severity: Medium — key-store integrity and credential availability under concurrent lifecycle operations.**

**Confidence: High** for the absence of cross-process synchronization and the non-transactional write sequence; the specific interleaving is a source-derived race scenario and should be confirmed with deterministic concurrency tests.

## Boundaries

- The phase documentation already states that callers must serialize operations; this report concerns the absence of enforcement in the provider and supplied CLI, not an undocumented guarantee.
- This finding is distinct from partial-commit capacity exhaustion, active-key precondition validation, and HMAC backup continuity.
- No production OS credential store was modified and no runtime tests were executed.
