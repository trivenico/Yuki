# Phase 196 — MCP execution lifecycle audit

## Delivered

- Extend the existing SQLite approval audit event vocabulary with execution_started, outcome_unknown, executed and execution_failed.
- Add MCPToolPolicyAudit and MCPExecutionAudit adapters that records event type, hashed owner, hashed per-invocation correlation ID, timestamp, hash-chain links, and bounded provider/account/tool identity.
- Do not persist arguments, credentials, remote results, or raw owner identifiers.
- Make policy-bound OAuth MCP client construction require an execution-audit callback.
- Emit execution_started durably before dispatch. If that audit write fails, do not send the remote call.
- Emit executed only after a successful response; explicit JSON-RPC errors and MCP tool isError results are failures.
- Classify transport/protocol ambiguity after dispatch as outcome_unknown; do not retry.
- Bind all lifecycle events to a random per-invocation ID.

## Semantics

Approval is an authorization decision, not execution success. execution_started means Yuki durably recorded intent immediately before dispatch. executed means a valid success response was received and the completion audit was persisted. A transport loss, malformed response, or other ambiguous post-dispatch exception is outcome_unknown. Yuki must not automatically retry unknown outcomes.

A durable audit failure after a remote success response means the caller receives an error rather than a success claim; operators must reconcile the remote account before retrying.

## Scope

This phase adds lifecycle instrumentation at the MCP client boundary. It does not connect any provider, activate tools, implement provider-specific idempotency, or make income actions autonomous. External write and financial actions still require owner approval and platform-specific terms compliance.

## Validation

CI is authoritative. No local test execution is claimed.
