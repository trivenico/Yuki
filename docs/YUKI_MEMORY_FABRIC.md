# Yuki Memory Fabric (YMF)

## Purpose

Yuki Memory Fabric is a tiered working-set manager for workloads that exceed the machine's available fast memory. It uses physical system RAM for hot data and an SSD-backed cache for cold or resumable data. It is a storage and placement layer, **not a conversion of SSD capacity into RAM**.

YMF must never advertise SSD capacity as physical RAM, promise RAM-like latency, or transparently page arbitrary Python objects. SSD access is slower and has different endurance, latency, and failure characteristics.

## Memory tiers

1. **CPU RAM** — active execution state, hot cache entries, small working sets.
2. **GPU VRAM** — tensors, model layers, and active accelerator working sets. Managed by a future accelerator adapter; YMF core does not assume CUDA or a GPU.
3. **SSD cache** — cold model blocks, reusable tensors/blobs, checkpoints, and paused-agent state. SSD cache entries are disposable and must be reloadable or recomputable.
4. **Persistent knowledge/storage** — authoritative user data, databases, vector memory, and audit records. This is not an evictable cache and is outside YMF's cache API.

## Initial MVP boundary

The first implementation is a bounded, thread-safe byte cache:
- explicit namespace and key; no pickle or arbitrary object deserialization;
- configurable RAM and SSD byte budgets;
- RAM LRU for hot entries and SQLite-backed SSD storage for cold entries;
- SHA-256 integrity checks on disk reads;
- per-entry TTL and bounded namespace/key/value sizes;
- deterministic LRU eviction when SSD budget is reached;
- cache misses return `None`; corruption raises a dedicated error and removes the invalid cache row;
- stats expose budgets, resident bytes, disk bytes, hits, misses, evictions, and corruption count;
- the cache stores only reconstructible data, never authoritative conversation history, credentials, tokens, or approval/audit records.

## Placement policy

A workload adapter decides whether a payload is safe to cache and whether it should be placed in RAM, SSD, or both. YMF does not inspect arbitrary model internals. Future adapters may implement:
- model-layer/tensor offload for supported runtimes;
- CPU/GPU staging and prefetch;
- checkpoint and resume for paused agents;
- workload-specific cache admission and priority;
- hardware discovery and memory-pressure signals shared with Yuki Compute Fabric.

The default must be conservative: no GPU memory operations without a backend that explicitly supports the model/runtime and device.

## Safety and operational requirements

- Never treat cached content as instructions or trusted policy.
- Never cache secrets or user data unless a separately reviewed encryption, retention, and consent policy explicitly permits it.
- Restrict the cache database to the current user's private Yuki data directory; follow existing desktop storage permission checks.
- Bound all inputs, cache sizes, TTLs, and namespace quotas.
- Cache entries are disposable. Cache loss must not corrupt authoritative Yuki data.
- Use SQLite transactions and a process/thread-safe connection strategy; do not share SQLite connections across threads.
- Surface disk-full, permission, and corruption failures clearly; do not silently claim a successful SSD write.
- Do not silently replace OS swap/pagefile settings. OS virtual memory remains the operating system's responsibility.

## Expected outcome

YMF can reduce repeated loads and keep cold, reconstructible data off the active RAM working set. It can make Yuki more resilient on low-RAM CPU-only machines and prepare for model/tensor offload. It cannot make an SSD behave like DRAM, increase GPU VRAM, or guarantee that a model larger than available memory will run efficiently. Actual gains depend on access patterns, SSD performance, model/runtime support, and how much data must be moved.
