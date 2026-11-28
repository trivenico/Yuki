# Provider routing factory reconfiguration race audit

Date: 2026-11-28
Repository: trivenico/Yuki
Reviewed revision: 4607df3248c4a30307ec8804ccaf19b6c97d96b3

## Finding

**P2 — A concurrent provider registration can invalidate the privacy decision made for a routed adapter.**

The active provider registry in `yuki_providers.py` protects registration and provider selection with `_lifecycle_lock`, but routed adapter construction does not hold that lock across policy selection and factory resolution.

The relevant flow in `ProviderManager.create_routed_adapter()` is:

1. Call `self.routing_registry().route(... allow_cloud=allow_cloud ...)`. The registry is built from the sanitized provider catalog and records the selected provider's privacy classification.
2. Call `self._build_adapter(selected.provider)` outside `_lifecycle_lock`.
3. Probe availability and later acquire `_lifecycle_lock` only to publish the adapter into `_routed_adapters`.

Meanwhile, `ProviderManager.register()` acquires `_lifecycle_lock` and can replace the factory for an existing provider name. The method does not coordinate with an in-progress routed construction or preserve a factory/capability generation for the routing decision.

### Concrete interleaving

- A request calls `create_routed_adapter(allow_cloud=False)`.
- Its routing registry snapshot classifies provider `custom` as local and chooses it.
- Before `_build_adapter("custom")` resolves the factory, another thread calls `register()` for the same provider name and replaces its factory with a cloud-backed implementation.
- The first request builds the newly registered adapter and publishes it. The adapter is checked only for generic `generate()` / `is_available()` behavior and availability; its actual privacy class is not checked against the earlier routing decision.

The resulting adapter can therefore contradict the privacy policy used to choose it. This is not merely a stale display/catalog result: the returned adapter may send request content to a cloud provider even though the caller explicitly set `allow_cloud=False`.

The gap is also widened by `list_providers()`, which iterates `_providers` and reads `_factories` without acquiring `_lifecycle_lock`. A concurrent registration can make catalog construction observe an inconsistent registry snapshot (or raise during dictionary iteration); `build_provider_catalog()` catches manager-list errors and falls back to its static implementation metadata, which is not a safe substitute for a coherent runtime registry snapshot.

## Why existing protections do not close this gap

- `register()` and `select()` are individually serialized.
- `lease_adapter()` protects the lifetime of an already-owned adapter.
- `create_routed_adapter()` checks that the manager is still open before publishing.
- The adapter contract validation checks only the generic provider interface.

None of these guarantees that the factory used to instantiate an adapter is the same factory/capability generation whose metadata was used for the privacy decision.

## Recommended remediation

Make routing selection and factory resolution one atomic, versioned operation:

1. Under `_lifecycle_lock`, build a coherent provider/capability snapshot and capture the selected provider's registered factory plus a monotonically increasing registry generation.
2. Resolve the adapter using that captured factory, not by looking up the mutable `_factories` mapping later.
3. Before publishing, reacquire the lock and verify that the provider registration generation is unchanged. If it changed, close the newly constructed adapter and fail closed (or restart routing from a fresh snapshot).
4. Ensure the captured registration includes an explicit trusted privacy classification, and validate that classification before returning the adapter. Do not infer privacy from a provider name or a caller-supplied display label.
5. Protect `list_providers()` and any registry/catalog snapshot operation with the same lock, or expose an immutable snapshot produced under the lock.
6. Add a deterministic concurrency regression test using events/barriers: pause after route selection, replace a local provider factory with a cloud implementation, resume construction, and assert that no adapter is returned under `allow_cloud=False` and that any rejected candidate is closed exactly once.

A per-registration generation token is preferable to holding the lifecycle lock while invoking arbitrary provider factory code, since constructors may block or re-enter application code. If construction is performed outside the lock, the factory and privacy metadata must first be captured immutably, then generation-checked before publication.

## Scope and limitations

This finding is based on source inspection of `yuki_providers.py`, `providers/catalog.py`, and `providers/capabilities.py` at the reviewed revision. No concurrent execution or provider request was performed. No tests were run. The audit does not establish that a production deployment currently has a caller capable of re-registering providers concurrently; it establishes that the manager's public registration and routed-construction operations do not enforce a coherent policy/factory snapshot when they overlap.

## Acceptance criteria

- A route made with cloud routing disabled cannot return an adapter whose trusted registration metadata classifies it as cloud or third-party.
- Concurrent registration either leaves a route bound to its original immutable factory/metadata snapshot or causes construction to fail closed after a generation mismatch.
- Catalog listing never silently substitutes static provider metadata for a failed or inconsistent live-registry snapshot.
- Rejected constructed adapters are closed exactly once, with cleanup failures preserved without masking the primary routing failure.
- Deterministic race tests cover local-to-cloud replacement and registration removal/replacement during adapter construction.
