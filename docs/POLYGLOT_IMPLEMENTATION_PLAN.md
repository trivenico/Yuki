# Yuki polyglot implementation plan

## Delivery model

Yuki will use separate, independently reviewable language workstreams where a
language provides a measurable capability that the existing Python/TypeScript
implementation does not provide efficiently or safely. Each workstream owns a
narrow interface, its own tests and CI gate, and a separate draft PR. Integration
must preserve the current Python application and authorization boundaries.
This avoids a multi-language rewrite and lets independent work proceed without
editing the same modules.

## Workstreams

| Language | Bounded responsibility | Integration contract | Tracking |
|---|---|---|---|
| Python | Product core, native intent/planning, memory, permissions, workflow state, application composition | Authoritative runtime and policy | Existing core / PR #1099 |
| TypeScript / JavaScript | Web client, browser API contracts, dashboard interactions | Typed HTTP/API boundary | Existing web CI |
| C++ | Local inference runtime through llama.cpp | Optional provider adapter | Existing local-inference provider |
| Rust | Offline classic-PCAP framing validation accelerator | Optional PyO3 module; Python fallback | [Issue #1100](https://github.com/trivenico/Yuki/issues/1100), [PR #1106](https://github.com/trivenico/Yuki/pull/1106) |
| Go | Distributed worker process supervision and health telemetry | Authenticated existing Python worker-service contract | [Issue #1101](https://github.com/trivenico/Yuki/issues/1101) |
| C | Read-only firmware image header inspection | Optional Python extension with pure-Python fallback | [Issue #1102](https://github.com/trivenico/Yuki/issues/1102) |
| C# | Windows OS credential-store adapter | Existing Python credential-vault interface | [Issue #1103](https://github.com/trivenico/Yuki/issues/1103) |
| Java | Typed JVM integration for selected MCP-connected enterprise systems | Existing authenticated Python MCP/OAuth boundary | [Issue #1104](https://github.com/trivenico/Yuki/issues/1104) |
| Ada/SPARK | Formal proof of a narrow approval/authorization state machine | Versioned conformance vectors; Python remains runtime authority initially | [Issue #1105](https://github.com/trivenico/Yuki/issues/1105) |

## Dependency and parallelization

The Rust PCAP and C firmware-header workstreams are independent offline parser
projects and can proceed separately once their input/output contracts are
stable. Go worker supervision can proceed against the existing worker-service
contract without changing task persistence. C# credential storage and Java MCP
integration depend on the current vault and OAuth interfaces, not on Rust/Go.
Ada/SPARK can specify and prove the approval state machine independently using
conformance vectors.

The integration order is:
1. Freeze and test each narrow interface.
2. Implement language-specific library/service plus unit tests.
3. Add contract tests against the existing Python boundary.
4. Add dedicated language CI and reproducible build instructions.
5. Benchmark native accelerators and retain Python fallbacks until a measured
   benefit and full parity are demonstrated.
6. Integrate only after exact-head CI, security review and failure-mode review.

## Production rules

- Python remains authoritative for identity, ownership, permissions, approvals,
  audit, workflow state, memory and execution policy.
- A native component cannot grant itself capabilities or bypass an approval.
- Optional native components fail closed and have a tested Python/degraded path.
- No duplicate task database, OAuth/credential authority, or permission engine.
- C/C++/Rust parsers accept bounded local input; they do not capture, transmit,
  execute or modify firmware/network payloads.
- Each language must justify its runtime, packaging, maintenance and CI cost.
- No empty scaffolding or language adoption for appearance.
- Every release claim must match tested, measured behavior; a passing unit test
  is not a substitute for deployment, load, security and recovery validation.
