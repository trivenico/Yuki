# Browser worker execution contract

Status: design contract only. This document does not implement a dispatcher, IPC transport, durable lease store, audit sink, network sandbox, or browser worker. The current `automation/browser_worker_protocol.py` validates bounded JSON message shape only.

## Trust boundaries

- The owner-facing Yuki service is the policy decision point. It authenticates the owner, evaluates policy, obtains any required exact-action approval, and records authorization before dispatch.
- A trusted deployment-owned launcher owns the worker image digest, OS identity, mounts, environment, network policy, resource limits, profile lifecycle, and termination. The request payload cannot select or override these.
- The browser worker is untrusted with respect to authorization and page content. It receives no Yuki database, provider credentials, cloud credentials, host filesystem, Docker socket, shell, or audit-store access.
- Worker output and page text are untrusted input. The trusted service performs prompt-injection classification and records results; worker output cannot create, alter, or satisfy approval.

## Authorized task envelope

The control plane must construct an immutable envelope after policy and approval checks. The worker's current `BrowserTaskRequest` is only the bounded task payload, not an authorization token. The envelope must bind:

- protocol version, unique task ID, owner identity, exact request digest, exact normalized URL and permitted host policy revision;
- approval ID and digest of the approved action, when approval is required;
- worker image digest and launcher policy revision;
- creation time, absolute expiry/deadline, one-time nonce, and durable lease generation.

The trusted control plane—not an untrusted caller or worker—must populate the request's `policy_revision` from the policy decision and construct the request payload. The launcher verifies the envelope and payload against durable control-plane state before starting the worker. The worker cannot supply or amend owner identity, approval, policy, image, lease, or audit fields. Reject expired, mismatched, unknown-version, duplicate, or already-consumed envelopes.

## Durable dispatch and replay behavior

The control plane must use a durable compare-and-swap state machine:

`AUTHORIZED -> CLAIMED -> RUNNING -> SUCCEEDED | FAILED | CANCELLED | EXPIRED`

- Persist the authorization/audit event before launch. Claim is an atomic one-time transition bound to task ID, nonce, request digest, and lease generation.
- A duplicate submission with the same task ID and digest returns the recorded state/result; it never launches another worker. Reuse of the ID with a different digest is a conflict.
- A retry is a new task ID, nonce, lease generation, and fresh authorization/approval where required. Do not reset a terminal task to runnable.
- Persist terminal result and audit evidence before acknowledging completion. If durable state or audit is unavailable, fail closed and do not dispatch.
- A process-local set, in-memory lock, or worker-supplied timestamp is not replay protection.

## Cancellation, deadlines, and races

- Cancellation is a durable control-plane transition. The worker cannot clear or override it.
- The launcher enforces the absolute deadline and cancellation by terminating the entire worker process group/container/cgroup, then verifies exit and cleanup.
- Completion and cancellation compete through one atomic terminal transition. If cancellation is committed first, a late worker result is discarded as non-authoritative. If completion commits first, cancellation reports already terminal.
- Heartbeat loss, lease expiry, timeout, or launcher restart must trigger reconciliation and terminate any surviving worker before a replacement can claim the task.
- A cancelled or expired task is never resumed with the same nonce. Any retry receives a new task identity and authorization.

## Linux MVP IPC and resource enforcement

Use a private Unix-domain socket owned by the launcher, with restrictive directory/socket permissions and peer-credential verification. Frames must be length-prefixed and capped before allocation; parse strict JSON, reject duplicate/unknown keys, and enforce monotonic deadlines. IPC exposes only submit/status/cancel/result operations with typed schemas—never arbitrary command, path, environment, container flags, or CDP endpoint access.

The launcher—not the worker—sets CPU, memory, process, wall-time, output, and network limits. Enforce outbound policy at connection time through a trusted egress gateway/firewall/resolver boundary; hostname checks and preflight DNS lookups alone do not prevent DNS rebinding or SSRF. Block metadata/private/link-local/loopback destinations and revalidate every redirect and connection.

## Result and audit contract

The worker may return only bounded task ID, outcome, final URL, HTTP status, title, extracted text, and elapsed time. Treat all returned page-derived fields as untrusted. The trusted control plane must correlate the response task ID with the active durable dispatch and independently validate the final URL against the exact approved host/redirect policy before accepting the result. A syntactically valid HTTPS URL is not proof that navigation was authorized or that the browser connected to a permitted address; connection-time egress controls remain authoritative. Never use worker-returned `policy_revision`, URL, status, or content as evidence of authorization.

Do not return cookies, request/response headers, local paths, environment, browser profile data, raw CDP handles, or credentials.

The durable audit record must bind task ID, owner, request digest, approval reference, policy revision, launcher/worker image digests, lease generation, dispatch and terminal timestamps, cancellation/timeout outcome, and result digest. Avoid recording secrets or unnecessary page content. Result retrieval is idempotent and does not rerun the task.

## Enablement gates

Do not enable unattended browser execution until all of the following exist and pass integration/failure-injection tests:

1. authenticated IPC and trusted launcher with peer verification;
2. durable CAS lease/nonce store and replay tests across independent processes;
3. cancellation/deadline race tests and orphan-worker reconciliation;
4. connection-time egress enforcement, including redirect, DNS rebinding, private-IP and metadata-address tests;
5. OS-enforced worker identity, resource limits, disposable profile, and cleanup verification;
6. durable pre-dispatch and terminal audit with fail-closed behavior;
7. tests proving worker output cannot authorize actions and all page text remains untrusted.

Until then, keep the existing browser skill read-only and approval-gated. Protocol serialization tests are not evidence that these deployment controls exist.
