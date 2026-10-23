# Phase 91 — specialist integration with authorized read-only tools

## Scope

This phase adds an explicit adapter that lets a specialist task request bounded reads from Yuki's existing workspace tool registry. The adapter is designed for the current text-only specialist provider and durable worker model; it does not introduce autonomous model-driven tool calling.

## Request contract

The task argument is a JSON object with:
- `task`: required bounded text describing the analysis.
- `tool_calls`: optional list of at most five calls. Each call has exactly `name` and `arguments`.

Only registered tool names explicitly present in the adapter's allow-list are accepted. The caller supplies a trusted principal resolver from server/service configuration; actor identity and permissions are never accepted from task JSON. The underlying tool registry independently checks trust, `workspace:read`, safe paths, output bounds, and audit availability.

## Safety boundaries

- Read-only tools only: list, read, and search.
- No write, shell, process execution, network access, or Git mutation.
- Model output is plain text; the adapter never executes tool calls suggested by the model.
- Workspace content and task descriptions are untrusted data and are labelled as such in the provider prompt.
- At most five calls; task input, per-result size, total evidence size, and provider output are bounded.
- Tool denials, malformed calls, and unavailable audit logging fail closed.
- Worker tasks remain subject to the existing task authorization and approval gate. Use approval for sensitive or business-impacting requests.

## Integration

Construct the adapter with an already-selected provider, an `AuthorizedReadOnlyTools` instance, and a trusted service-principal resolver. Register the returned callable under the intended specialist ID in `SpecialistTaskWorker.handlers`. The workflow request must serialize its task and explicit read calls using the contract above.

## Validation

The dedicated test suite covers authorized file reads and search, allow-list enforcement, missing permission, untrusted principals, path traversal/secret-file rejection, malformed/oversized call batches, and provider failures. CI runs the adapter, worker, authorized-tool, runtime, and workflow-safety tests on Python 3.11 across Ubuntu, macOS, and Windows. Merge only after exact-head checks and repository quality gates pass.
