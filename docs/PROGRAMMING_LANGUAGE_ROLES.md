# Yuki programming-language responsibilities

## Current verified implementation

Python is the primary and currently verified application language. The root
`pyproject.toml` defines the installable package, CLI entry point
(`yuki`), dedicated worker entry point (`yuki-workers`), and the
`automation`, `core`, `providers`, `skills`, `ui`, `voice`, and
related Python packages.

The web client now has a narrow TypeScript API contract (`ui/web/chat-api.ts`)
compiled to a browser runtime and loaded by the existing chat UI. Its strict
TypeScript compile, generated-output consistency check and Node runtime tests
have a dedicated GitHub Actions gate. This is a verified TypeScript integration
for the browser API boundary, not a claim that the entire frontend has migrated
to TypeScript.

Rust, Go, Java, C#, C, and Ada/SPARK remain future integration targets. C++ now also underpins the optional in-process llama.cpp inference runtime via its Python native binding; Yuki does not yet maintain a custom C++ inference engine.
Do not claim a language is integrated until it has source code, a real caller,
contract tests, and a CI build/proof gate.

## Responsibility map

| Language | Responsibility | Boundary |
|---|---|---|
| Python | Application composition, agent orchestration, providers, memory, task store, worker scheduling, API, authorization, audit, desktop/voice integration | Authoritative control plane; owns identity, permissions and durable state |
| JavaScript / TypeScript | Browser UI, interactive dashboards, browser automation and frontend state | Calls authenticated Yuki APIs; cannot authorize actions or write trusted task state directly |
| Rust | Optional native isolation helpers, safe parsers, bounded high-performance services | Versioned IPC/FFI contract; no direct bypass of Python policy |
| Go | Horizontally scalable worker daemons, scheduler and infrastructure services | Authenticated service identity, leases, bounded task envelopes and durable audit through Yuki APIs |
| C | Narrow operating-system and hardware interfaces | Minimal ABI; explicit memory ownership and input validation |
| C++ | Native llama.cpp inference backend (optional, through the `llama-cpp-python` binding), plus future measured compute-heavy kernels | The native library runs in-process; Python retains orchestration, permissions and model/tool boundaries |
| C# | Windows services, OS integration and .NET enterprise connectors | Uses the same authenticated task and capability contracts |
| Java | JVM enterprise integrations and connectors | Uses the same service identity, API schemas and audit contract |
| Ada / SPARK | Formal verification of selected authorization, ownership, state-transition and resource-bound invariants | Small verified kernel with proof obligations; Python remains the orchestrator |

## Cross-language integration rules

1. Python remains the source of truth for user identity, account ownership,
   authorization, approval, task state, and audit events.
2. Cross-language boundaries use versioned JSON/HTTP or gRPC contracts first.
   Use FFI only when measured latency or platform requirements justify it.
3. Every service authenticates with a distinct service identity. Never trust
   owner IDs, task IDs, capabilities, or workspace paths supplied by a model.
4. Validate schemas, sizes, timeouts, retries, and error envelopes at both
   sides of a boundary. Fail closed on unknown versions and malformed data.
5. Native components receive only the minimum data and filesystem handles
   required for their operation. Secrets are never passed through task prompts
   or audit payloads.
6. Each new language needs a real production responsibility, unit and
   contract tests, dependency/security scanning, reproducible build, and CI
   gate before it is called integrated.
7. Do not introduce empty language scaffolds or nine parallel runtimes just
   to claim language coverage. Adopt languages incrementally when a concrete
   subsystem benefits.

## Adoption sequence

1. Keep Python core stable and finish task ownership, sandbox lifecycle,
   recovery and security verification.
2. Add TypeScript only when the web product has a defined frontend boundary.
3. Add Rust for a specific native security/performance component.
4. Add Go when worker throughput or independent deployment requires a
   distributed worker service.
5. Add C/C++ only for platform interfaces or profiled hotspots.
6. Add C# and Java for concrete Windows/.NET and JVM integrations.
7. Add Ada/SPARK for invariants whose assurance value justifies formal proof
   and toolchain maintenance.

This plan adds capabilities without requiring a wholesale repository
reorganization.


## Rust: bounded native parsing

Rust is being introduced as an optional, narrow native accelerator for the
offline classic-PCAP framing validator in `native/pcap_guard`. The Python
parser remains the public API and fallback; Python retains file access,
protocol dissection, evidence policy and authorization. See
[Rust PCAP guard](RUST_PCAP_GUARD.md). This is a measured, additive integration,
not a rewrite of the existing Python packet-analysis stack.
