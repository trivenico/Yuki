# Phase 29 — Compartmentalized security, privacy routing, and post-quantum readiness

## Goal

Design Yuki as a capable personal agent while assuming models, prompts, skills, retrieved documents, plugins, and remote services can be compromised. This phase defines an architecture and staged implementation plan; it does not claim that Qubes, Tor, Whonix, or post-quantum cryptography has already been integrated.

## Security domains

1. **Trusted control plane** — small, auditable policy service. It owns user identity, approval decisions, task state, secret references, and audit policy. The model never gets direct access to this plane's database or host privileges.
2. **Planner/model domain** — local or remote model inference. Treat model output as untrusted proposals, not commands.
3. **Untrusted browsing/retrieval domain** — fetches websites, email, attachments and other hostile content. No credentials or direct host filesystem access.
4. **Skill execution domain** — one bounded job per isolated worker/container/VM, with a read-only base image, dedicated scratch workspace, explicit capabilities, network disabled by default, CPU/memory/process/output limits and hard timeout.
5. **Network egress domain** — explicit per-task egress policy. Tor/Whonix routing is optional for privacy-sensitive browsing, not a universal default or a substitute for authorization.
6. **Secrets/signing domain** — secrets are held by OS keychain or dedicated secret manager; signing keys are not exposed to model context or worker environments.
7. **Device gateway** — narrow authenticated API for Android and desktop clients; device actions require scoped permissions and user approval appropriate to risk.

## Qubes OS / Whonix / Tor

- On compatible dedicated desktop hardware, Qubes OS can provide VM-based compartmentalization. Consider separate qubes for the Yuki control plane, untrusted browser/retrieval, development, and skill execution.
- Use Qubes/Whonix's documented network separation for tasks explicitly configured for Tor. Avoid custom Tor routing if an established Whonix configuration meets the need.
- Do not run the trusted control plane in the same disposable/untrusted domain as web content or arbitrary skills.
- Qubes is a host OS deployment option, not a portable library. On Windows/macOS/Linux hosts and Oracle Cloud Linux, use platform-appropriate isolation (VMs, hardened containers, seccomp/AppArmor where supported, least-privilege service accounts, mandatory access controls and network policy).
- Tor hides some network metadata from some observers; it does not guarantee anonymity, prevent application-level identification, or protect a compromised endpoint. Do not route account-authenticated or sensitive workflows through Tor by default.

Official references:
- Qubes architecture: https://github.com/QubesOS/qubes-doc/blob/main/developer/system/architecture.rst
- Qubes security goals: https://qubes-os.readthedocs.io/en/latest/developer/system/security-design-goals.html
- Qubes/Whonix privacy guidance: https://qubes-os.readthedocs.io/en/latest/introduction/faq.html

## Post-quantum cryptography (PQC)

- Build crypto-agility first: inventory algorithms, protocol versions, key types, certificate formats, and rotation paths; avoid custom cryptography.
- For key establishment and signatures, evaluate standardized NIST ML-KEM (FIPS 203), ML-DSA (FIPS 204), and SLH-DSA (FIPS 205) through maintained, vetted libraries and supported protocol stacks.
- Do not implement the algorithms directly in application code or claim the entire system is quantum-proof. Protocol interoperability, library maturity, key management, downgrade resistance, backups and migration plans matter.
- Prefer platform-supported hybrid classical/PQC TLS or secure messaging when available and appropriate; document compatibility and threat model before enabling.
- Never store raw API keys, bearer tokens, private keys, or decrypted secrets in prompts, logs, task payloads or audit events.

Reference: https://www.nist.gov/cybersecurity-and-privacy/what-is-post-quantum-cryptography

## Jarvis-style intelligence without unrestricted authority

- Multi-agent roles (planner, researcher, coder, reviewer, operator) run as isolated workers with typed messages and explicit capability grants.
- The planner can propose actions; a policy engine independently authorizes them.
- Every tool has a schema, risk classification, timeout, resource budget, data classification and audit policy.
- Memory is partitioned by user/project/security domain with retention controls, provenance, deletion support and injection-resistant retrieval.
- Self-improvement creates a patch in an isolated workspace, runs tests and security checks, and opens a reviewable PR. Protected policy, auth, sandbox and secret-handling code requires human review.
- Proactive/background tasks use explicit schedules and quotas; no silent monitoring, credential harvesting, or unauthorized device control.
- Device operations use allow-listed commands and explicit user confirmation for destructive, financial, privacy-sensitive, or physical-world effects.

## Implementation phases

### 29A — Baseline and threat model
- Inventory current runtime, open PRs, privileges, network egress, secrets, dependency and CI state.
- Add threat model, data-flow diagram, and security acceptance criteria.
- Do not merge stale branches wholesale; reconcile PRs one by one against main.

### 29B — Portable sandbox enforcement
- Verify real Docker/VM enforcement rather than mocked configuration.
- Enforce non-root execution, read-only root, isolated workspace, no network by default, bounded output/resources/time, and process cleanup.
- Add adversarial tests and platform capability reporting. Fail closed when requested isolation cannot be enforced.

### 29C — Policy and durable execution
- Bind approval to an immutable action/argument digest, expiry, identity and one-time nonce.
- Integrate durable task state with executor and audit trail.
- On uncertain external side effects, enter recovery_required and reconcile through downstream idempotency or status queries.

### 29D — Optional Qubes/Whonix adapter
- Document supported qube layout and narrow inter-qube RPC contracts.
- Make integration optional and detect whether the host is actually Qubes; never assume Qubes APIs exist elsewhere.
- Add integration tests on a real Qubes test host before claiming support.

### 29E — PQC readiness
- Create crypto inventory and migration plan; add algorithm/protocol agility tests.
- Integrate only vetted libraries and protocol-supported modes after compatibility review.
- Test key rotation, downgrade prevention and mixed-client interoperability.

### 29F — Jarvis capabilities
- Add specialist agents through the same task/permission/runtime contracts.
- Add grounded memory, multimodal interfaces, user-approved automation and device adapters.
- Measure capability with deterministic evaluations, adversarial prompt-injection tests, latency/cost budgets and user-control tests.

## Required security tests

- Prompt injection in web content cannot change tool permissions.
- A compromised skill cannot read host secrets or another task's workspace.
- Network egress is blocked unless explicitly granted.
- Approval tokens cannot be replayed or reused with changed arguments.
- Audit failure blocks new actions; uncertain post-action failures require reconciliation.
- Qubes inter-domain calls are allow-listed and validate all message fields.
- Tor routing status is verified for a privacy-routed task; failure is explicit and does not silently fall back to clearnet.
- PQC settings reject downgrade or unsupported algorithms and never log secret material.
- Self-improvement cannot silently change protected policy/sandbox files or merge its own high-risk changes.

## Exit criteria

Do not describe Yuki as production-ready, Qubes-isolated, anonymous, or quantum-resistant until the corresponding controls are implemented, exercised in a representative environment, and backed by reproducible test evidence. This document is a design plan only.
