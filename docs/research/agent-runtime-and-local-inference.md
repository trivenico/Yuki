# External architecture review: agent runtime and local inference

Reviewed 2026-11-15:
- https://github.com/cosmicstack-labs/mercury-agent
- https://github.com/drumih/turbo-fieldfare

This is an architecture assessment, not an endorsement or a claim that Yuki already implements these capabilities. Reuse concepts only after threat modelling, compatibility checks and independent tests. No third-party source code or model assets are copied.

## Mercury Agent: patterns worth adapting

Mercury is a persistent, channel-agnostic agent runtime with permission-mediated tools, local structured memory, budgets, schedulers and specialist agents. Useful patterns:

1. **One durable runtime, multiple thin interfaces.** Keep CLI, desktop, web and messaging channels as adapters to a shared application runtime. Avoid independent agent brains and duplicate persistence.
2. **Immutable, scoped permission context.** Freeze principal, channel, role, allowed tools and filesystem scopes for each execution. Delegated agents must never receive more authority than their parent. Deny rules win; approvals fail closed.
3. **Real specialist isolation.** Give each agent its own model selection, tool registry, permission context, memory namespace, budget and lifecycle. Avoid shared mutable registries and implicit global defaults.
4. **Durable work, not fire-and-forget.** Persist task input before acknowledgement; use idempotency keys, leases, bounded retries, dead-letter inspection, cancellation and append-only run history. Restart recovery must be explicit.
5. **Bounded scheduling and resource governance.** Start with one process and bounded async concurrency, separate user-reply and background lanes, per-agent/global budgets, and visible load shedding.
6. **Structured, governed memory.** Typed facts with confidence, provenance, importance, conflict resolution, owner scoping and retention beat indiscriminate transcript stuffing. Cross-agent recall should be opt-in and read-only by default.
7. **Evidence-based completion.** Coding and business tasks are complete only after required tests, validation or external confirmation succeeds.

### Mapping to Yuki

| Pattern | Yuki component boundary |
|---|---|
| Runtime and channel adapters | application-composition, operator-interface, conversation-api |
| Permission context and approval | identity-access, guarded-actions |
| Specialists and supervisor | agent-execution, durable-workflows |
| Durable queue, journal and scheduler | durable-workflows |
| Scoped long-term memory | memory |
| Budgets and resource policy | agent-execution, configuration-runtime |
| Evidence-based completion | durable-workflows, readiness and CI |

Do not introduce a parallel Mercury runtime. Extend Yuki's canonical contracts and retain the deny-by-default CLI boundary until each tool is independently reviewed.

## TurboFieldfare: patterns worth adapting

TurboFieldfare is a specialized Swift/Metal inference engine for Gemma 4 26B-A4B on Apple Silicon and macOS 26+. Its low-memory result is achieved through model-specific engineering, not a generic optimization applicable to every model or GPU.

1. **Memory-budgeted inference.** Define budgets for resident weights, KV cache, scratch buffers, expert cache and file cache. Reject configurations exceeding validated limits before model load.
2. **Selective expert streaming.** For compatible mixture-of-experts architectures, keep shared weights resident and stream selected experts into bounded reusable cache slots. This is model-specific and needs exact correctness tests.
3. **Bounded chunked prefill.** Process long prompts in bounded chunks to control peak activation memory and expose latency/memory trade-offs.
4. **Verified model installation.** Pin model revisions, stream bounded byte ranges, support resume, hash completed artifacts, validate manifests and atomically promote only complete model directories.
5. **Single model owner.** Avoid multiple processes loading the same large model. A dedicated inference service should own model memory and expose a narrow local API.
6. **Stable inference boundary.** An OpenAI-compatible loopback API decouples orchestration from one inference engine. Model-produced tool calls remain proposals; the caller authorizes and executes them.
7. **End-to-end benchmarks.** Measure cold/warm runs, time-to-first-token, decode, prefill, memory pressure, correctness and quality on target hardware. Preserve negative experiment results.

### Portability limits

TurboFieldfare currently targets Apple Silicon, Metal 4, Swift 6.2 and macOS 26+. It is not a direct implementation path for Yuki's Oracle Cloud Linux runtime, Windows portable bundle or Kaggle GPU environment. Its model format and Metal kernels are specialized. Yuki should define backend-neutral contracts first, then add separate adapters for validated hardware.

A future decomposition:
- provider-runtime: stable model capability and normalized streaming/cancellation contract.
- local-inference: optional dedicated inference service with explicit model ownership and health checks.
- model-artifacts: pinned manifests, checksums, resumable install and atomic promotion.
- resource-runtime: measured CPU/RAM/VRAM budgets and admission control.
- evaluation: correctness, quality, latency and memory regression suites per hardware profile.

Never expose a local inference server beyond loopback until authentication, TLS, authorization, rate limits and operational hardening are independently implemented.

## Yuki implementation sequence

1. Finish canonical component contracts and composition roots, including configuration, provider, agent, durable conversations and application lifecycle.
2. Model specialist execution and durable workflow state as first-class components; add scoped permission and memory contracts before autonomous agents.
3. Add model-artifact and inference-runtime contracts while keeping the provider interface stable.
4. Implement one backend adapter at a time. Add expert streaming only where model format and hardware justify it.
5. Require end-to-end CI, security tests, fault injection, benchmark evidence and rollback before marking a component implemented.

## License and provenance

At review time, Mercury Agent declares MIT and TurboFieldfare declares Apache-2.0. These licenses do not automatically cover separately hosted model weights, datasets, dependencies or trademarks. This review records architectural ideas and public facts only; no third-party source code or model artifacts were copied into Yuki.
