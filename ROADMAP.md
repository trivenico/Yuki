# Yuki production engineering roadmap

This roadmap tracks the gap between today's terminal prototype and the intended companion, developer, and defensive cybersecurity product.

## Phase 1 — Reliability and safety foundations

- [x] Add a minimal test suite and CI workflow.
- [x] Add a tool-name allow-list boundary.
- [x] Add metadata-only audit records for selected local actions.
- [x] Clarify that provider registry entries are not live integrations.
- [x] Bound and validate learning-history records.
- [ ] Verify the latest expanded test run passes.
- [ ] Add schema/version migration for persistent state.
- [x] Define the isolated developer-execution threat model, trust boundaries, abuse cases, required controls, residual risks, and backend acceptance gates.
- [ ] Add a repository-wide production release checklist and independently review the threat model.
- [ ] Add dependency pinning and static analysis when dependencies are introduced.

## Phase 2 — Real model/provider architecture

- [ ] Define provider adapter contracts, timeouts, cancellation, and structured errors.
- [ ] Implement one provider using its official API and supported authentication only.
- [ ] Keep credentials out of prompts, logs, repository files, and exception messages.
- [ ] Add explicit provider selection and capability reporting.
- [ ] Preserve offline mock tests and support local models where practical.

## Phase 3 — Agent execution engine

- [x] Add a bounded workflow state machine for perception-to-report lifecycle tracking.
- [x] Add a machine-readable catalog for 100 planned specialist capabilities with stable IDs and safe discovery.
- [x] Add read-only discovery that matches catalog proposals to explicitly registered, enabled runtime agents by exact capability ID.
- [x] Add a guarded worker factory that connects enabled catalog-backed agent registrations to explicit provider roles and the existing text-only worker; catalog presence alone never enables execution.
- [x] Enforce worker-factory runtime identity and `emit`-only execution compatibility against the authorized specialist registry.
- [x] Connect plan preparation, required-tool policy checks, approval requests, and lifecycle events in a non-executing orchestrator.
- [x] Add a bounded task-intelligence boundary for capability recommendations and explicit, reviewable DAG plan preparation.
- [ ] Connect the workflow to explicit plans, steps, deadlines, and budgets.
- [x] Add a versioned specialist result envelope with strict provenance boundaries and explicit unverified-output limitations.
- [x] Add read-only plan synthesis that preserves specialist/task provenance, reports incomplete work, and leaves conflict adjudication to human review.
- [x] Add deterministic structural quality checks for synthesis coverage, source traceability, duplicate references, and provenance labels without claiming semantic correctness.
- [x] Add explicit, bounded specialist disagreement records that require distinct source tasks and preserve unresolved positions for human review.
- [x] Assemble read-only engineering decision review packets that bind disagreements to source tasks and never authorize changes or choose a winner.
- [ ] Connect tool schemas, typed outputs, per-tool permissions, and approval gates to the workflow transitions.
- [ ] Add a separately reviewed execution adapter with cancellation, deadlines, bounded retries, and re-authorization immediately before every tool call.
- [ ] Add retry limits, idempotency, cancellation, and recovery from partial failure.
- [x] Prepare inert, digest-bound workspace change proposals only after a completed review and explicit human decision; application remains behind the existing trusted-principal and approval gate.
- [x] Render deterministic unified diffs for workspace proposals after verifying exact base-file SHA-256 digests and proposal content integrity; diff review remains read-only.
- [x] Add bounded, read-only AST impact hints for changed Python modules, direct/transitive importers, likely test candidates, configuration/dependency files, and security-sensitive paths; validate proposal integrity and surface syntax failures without executing code or tests.
- [x] Prepare deterministic regression-test selection hints with direct/transitive priorities and conservative full-suite recommendations for configuration, dependency, security-sensitive, or incomplete-analysis cases; planning never executes tests.
- [x] Add a regression-plan adapter that validates a single planned candidate and its runner allow-list; host execution is explicitly classified and blocked from this workflow until a genuine isolated runner is implemented.
- [x] Harden the Docker argv builder with digest-pinned images, pull-never behavior, bounded arguments, safe mount-path validation, no network, capability dropping, no-new-privileges, and bounded memory/swap/CPU/PIDs; add immutable sandbox policy values and a fail-closed capability declaration contract. All declarations are rejected until a trusted host-side probe/attestation is implemented; metadata cannot establish enforcement.
- [x] Add a bounded parser for Docker daemon info JSON that reports self-declared rootless/seccomp/MAC/cgroup metadata while explicitly listing controls it cannot verify; diagnostics are not attestation and do not enable execution.
- [x] Add a strict Docker CLI diagnostics preflight and bounded read-only invocation: explicit canonical executable and Unix socket, scrubbed environment, fixed argv, process-group cleanup, timeout, and stdout/stderr caps. Daemon output remains self-reported diagnostics, never attestation; no container is launched. Executable ownership and parent-directory write permissions plus world-writable socket rejection are also enforced.
- [x] Implement bounded read-only Linux procfs process observations for PID start identity, namespace links, selected privilege fields, and cgroup-v2 membership; reject symlink substitution for procfs records/directories; output is explicitly non-attesting and leaves enforcement controls unknown.
- [x] Add a separate read-only cgroup-v2 inspector for controller availability and leaf CPU, memory, swap, and PID limits; reject symlinks and malformed values, preserve unlimited limits explicitly, and never treat observations as attestation.\n- [x] Combine Linux process and cgroup observations in a schema-versioned diagnostic envelope with explicit timing, membership consistency checks, and unknown controls; it remains sequential, caller-rooted, non-atomic, and non-attesting.
- [x] Add a bounded Linux mountinfo observer for workload mount entries and reported read-only flags, with strict parsing and symlink rejection; reported flags remain diagnostic, not proof of write isolation.
- [x] Compare workload namespace identifiers against an explicitly supplied process snapshot for user, PID, mount, IPC, and network namespaces; shared versus distinct identifiers are reported without trusting the baseline or asserting isolation.
- [x] Inspect bounded Linux user-namespace UID/GID maps and `setgroups` policy, validating ranges and overlaps while keeping the result diagnostic-only.
- [x] Observe kernel-reported active LSM names, process security context, AppArmor profile mode, and SELinux global enforcing state as diagnostic-only evidence.
- [x] Inspect bounded process network-namespace interface counters, IPv4 routes, and IPv4 TCP listeners; document that route tables are not egress or reachability proof.
- [x] Inspect Linux IPv6 route-table records, validate IPv6 prefixes and flags, and report UP default routes without treating missing routes as proof IPv6 is disabled.

- [x] Extend read-only Linux network observations to IPv6 TCP listeners, UDP/UDP6 sockets, and Unix-domain sockets; preserve raw address representations where kernel byte order is ambiguous.
- [x] Define a defensive integration matrix for Nmap-, Metasploit-, mitmproxy-, Burp-, ZAP-, Caido-, sqlmap-, John-, Aircrack-ng-, Hydra-, Netcat-, Hoppscotch-, and Wireshark-inspired workflows; active testing remains gated by explicit scope, authorization, and verified isolation. See `docs/SECURITY_TOOL_INTEGRATION_MATRIX.md`.
- [x] Add an offline Nuclei JSONL report importer with bounded parsing, duplicate-key rejection, URL/target redaction, raw request/response and extracted-value omission, and explicitly unverified finding provenance. Template execution and live scanning remain disabled. See `docs/NUCLEI_INTEGRATION.md`.
- [x] Add a bounded offline classic-PCAP reader supporting all four classic-PCAP endian/timestamp variants; packet bytes and timestamp components are preserved, and malformed/truncated/oversized inputs are rejected. Protocol decoding and PCAPNG remain outstanding.
- [x] Add bounded offline Ethernet, ARP, IPv4/IPv6, TCP/UDP header metadata dissection for classic PCAP packet bytes; payloads are not retained or executed, unsupported link types and IPv6 extension headers remain explicitly undecoded.
- [x] Add bounded offline DNS message metadata parsing with compression-loop and section-bound checks; retain A/AAAA and bounded name records while suppressing TXT contents.
- [ ] Build the defensive Yuki Network Defense workspace: bounded offline PCAP/PCAPNG parsing, protocol metadata dissection, evidence-linked anomaly explanations, redaction, and audit history. See `docs/YUKI_NETWORK_DEFENSE_ARCHITECTURE.md`.
- [ ] Consider visible, owner-triggered live packet capture only after explicit consent, privilege minimization, stop controls, retention limits, redaction, and platform security review.
- [x] Specify evidence classes and an independent host-verifier protocol for sandbox attestation, including nonce/manifest/process binding, kernel-observed state, behavioral canaries, replay/expiry rules, and fail-closed unknowns; implementation and independent review remain outstanding.
- [ ] Implement trusted runtime probing/attestation and verify rootless daemon, namespaces, seccomp, mandatory access control, cgroups, mount, network, and cleanup enforcement; current capability declarations are metadata, not proof.
- [ ] Integrate a verified isolated backend with the regression-plan adapter; the host runner remains trusted-code-only and is not a sandbox. The separate opt-in hackback Docker runner is not accepted as the regression backend.
- [ ] Add sandboxed developer tasks and human-reviewable patches; the existing approval-gated test runner is not a sandbox and must not be treated as one.
- [ ] Treat external content as untrusted; defend against prompt injection.

## Phase 4 — Companion and memory

- [ ] Separate short-term context, long-term memories, and task history.
- [ ] Add memory inspection, export, deletion, retention controls, and consent.
- [ ] Add retrieval tests and relevance evaluation.
- [ ] Make personality adjustable without overriding safety policy.
- [ ] Never infer sensitive personal attributes for memory without clear user intent.

## Phase 5 — Defensive cybersecurity

- [ ] Define authorized target scopes and prove-of-authorization workflow.
- [ ] Begin with passive analysis of user-provided files and dependency/configuration checks.
- [ ] Add evidence-backed findings, severity rationale, and remediation suggestions.
- [ ] Require approval before active scanning, network interaction, or modifications.
- [ ] Test against intentionally vulnerable local fixtures; do not target third parties without authorization.

## Phase 6 — User experience and integrations

- [ ] Build a web dashboard with task state, permission prompts, audit history, and settings.
- [ ] Add desktop/terminal consistency and accessible interaction patterns.
- [ ] Add supported browser automation with visible controls and an isolated session.
- [ ] Add secure export, backup, and restore.

## Phase 7 — Evaluation and scale

- [ ] Create benchmark tasks for companion usefulness, developer correctness, and defensive security quality.
- [ ] Measure completion rate, cost, latency, safety violations, and recovery.
- [ ] Add regression suites and release gates.
- [ ] Evaluate multi-agent collaboration only when it improves measured outcomes.
- [ ] Research emerging and quantum computing approaches without claiming capabilities that have not been demonstrated.

## Release gate

A phase is not complete merely because code has been committed. It needs passing automated tests, a review of failure modes, clear capability documentation, and a reproducible verification result.
