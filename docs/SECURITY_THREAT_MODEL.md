# Yuki security threat model and post-quantum readiness

Status: engineering baseline, not an independent security audit. Revisit this document whenever new execution surfaces, integrations, or data stores are added.

## Security goals

1. Keep user credentials, private keys, personal files, prompts, and memory private by default.
2. Prevent untrusted model output and third-party skills from gaining authority by themselves.
3. Require explicit, scoped authorization for consequential actions.
4. Make failures visible, bounded, auditable, and recoverable without destructive automatic repair.
5. Make security claims testable and tied to the exact release.

## Assets

- Provider API keys, OAuth credentials, signing keys, and session tokens.
- User workspace files, local/vector memory, indexed documents, and backups.
- Approval decisions, task history, and security telemetry.
- Local model endpoints, cloud provider requests, plugin processes, voice input/output, and privileged security integrations.
- Release artifacts, dependency lock data, signing metadata, and update channels.

## Trust boundaries and adversaries

- User prompt and retrieved documents -> model: treat as untrusted data; defend against prompt injection.
- Model output -> planner/tool router: strict schema validation, registered capabilities only, action budgets, and fresh authorization.
- Plugin -> host process: current Python plugins execute in-process when explicitly trusted. An allowlist is not a sandbox. Do not enable untrusted plugins until worker isolation is implemented and tested.
- Local process -> cloud provider: explicit per-task opt-in, disclose what is sent, do not silently fall back from local to cloud.
- Agent -> OS/network: deny by default, path and destination allowlists, bounded time/resources, least privilege, and explicit approval for sensitive writes.
- Agent -> telemetry/logs: no raw prompts, completions, tool arguments, credentials, or personal document content. Telemetry is diagnostic, not an authorization source.
- Update/release -> user machine: dependencies and artifacts must be auditable; signatures alone do not establish that code is safe.

## Required controls before production claims

- Isolated plugin/command workers with OS-level filesystem and network policy; process timeout and resource limits; protocol validation.
- Exact-action approvals including action, target, normalized arguments, expiry, nonce/idempotency key, actor, and policy version. Reject replay and fail closed if approval services fail.
- Secure secret storage through OS keychain or a dedicated secret manager. Never place private keys in the repository, ordinary config, telemetry, or error messages.
- Threat-focused tests for prompt injection, path traversal, SSRF, credential leakage, permission bypass, resource exhaustion, corrupt state, and restart/replay behavior.
- Durable audit events with redaction, bounded retention, integrity protection, access control, and explicit deletion/retention policy.
- Dependency pinning/updates, vulnerability and secret scanning, SBOM, signed releases, provenance, reproducible build checks where feasible, and documented rollback.
- Independent review and a supported-platform test matrix.

## Post-quantum cryptography (PQC)

“Quantum-level” or “quantum-proof” is not a valid security guarantee. Quantum computers do not automatically break every cryptographic primitive, but sufficiently capable machines threaten widely used public-key algorithms such as RSA and elliptic-curve signatures/key agreement. Yuki's current agent identity helper uses Ed25519 and must not be described as post-quantum secure.

Migration plan:

1. Inventory every cryptographic use: identity signatures, transport TLS, package/release signing, encrypted backups, key exchange, and third-party SDKs.
2. Separate cryptographic interfaces from implementations; version signed envelopes and include algorithm identifiers so algorithms can be rotated without changing business logic.
3. Evaluate standardized post-quantum algorithms such as ML-DSA for signatures and ML-KEM for key establishment through vetted, maintained libraries and supported protocol stacks. Do not implement primitives manually.
4. For transition, evaluate a reviewed hybrid classical + post-quantum design. Define verification, downgrade prevention, key identifiers, key rotation/revocation, and compatibility rules before deployment.
5. Test interoperability, signature/key sizes, latency, memory use, backup/restore, rotation, revocation, and failure behavior. Pin library versions and monitor advisories.
6. Require an independent cryptographic review before describing Yuki as PQ-ready. Do not invent a custom “quantum encryption” scheme.

PQC does not replace access control, sandboxing, secure key storage, patching, or incident response. A quantum-resistant signature cannot make an unsafe plugin safe.

## Honest release language

Until the controls above are implemented and independently reviewed, describe Yuki as a security-conscious prototype with fail-closed approval primitives and partial output/telemetry safeguards—not as unbreakable, quantum-proof, fully autonomous, or production-ready.
