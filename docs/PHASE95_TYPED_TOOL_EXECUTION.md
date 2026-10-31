# Phase 95 — typed fail-closed agent tool execution

## Scope

Adds an opt-in typed dispatcher for model-proposed tool calls. It composes the existing immutable `ToolCall`, `ToolDefinition`, `ApprovalDecision`, and `ToolResult` contracts with per-invocation authorization.

## Invocation sequence

1. Require a typed `ToolCall` and look up an exact registered tool name.
2. Validate the argument object against the registered bounded JSON-schema subset.
3. Ask the configured policy to authorize this tool on this invocation. Policy errors fail closed.
4. Require an `ApprovalDecision` whose call ID and fingerprint match the exact call for side-effecting tools and all high-risk tools.
5. Invoke only the registered handler.
6. Bound serialized output, return a typed `ToolResult`, and emit metadata-only audit outcomes.

## Security properties

- Unknown tools and invalid arguments are rejected before handler invocation.
- Boolean-like/truthy policy values are not accepted; authorization must return literal `True`.
- Exact-call approvals cannot be replayed with changed arguments.
- Handler exceptions and non-JSON outputs do not leak exception details.
- Audit callback failures do not alter the authorization decision or result.
- Output size is bounded.

## Limitations

- This dispatcher executes handlers in the current Python process. It is **not a sandbox** and does not remove handler process privileges.
- It does not yet provide execution deadlines, cancellation, OS-level resource limits, or process/container isolation.
- It is not yet wired as the universal dispatch path for every CLI, desktop, plugin, skill, and specialist-worker call site.
- The current schema validator supports a deliberately limited JSON Schema subset; unsupported keywords fail closed.
- A trusted caller must supply an approval decision from the application's authenticated approval boundary. Constructing an approval object is not itself proof of user identity.

## Verification

    python -m unittest tests.test_typed_tool_runtime -v
    python -m unittest tests.test_task_contracts -v
    python -m unittest tests.test_capability_manifest -v
    python -m unittest discover -s tests -v

Capability status is `experimental` until integration, isolation, and production verification are complete.
