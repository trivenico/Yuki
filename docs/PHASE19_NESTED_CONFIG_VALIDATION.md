# Phase 19 — Strict Nested Onboarding Configuration

## Audit finding
The onboarding validator already rejected unknown top-level fields, but nested security/daemon/messaging objects accepted unknown keys. MCP server lists also allowed blank/duplicate tool names and duplicate endpoints. These cases can conceal typos or ambiguous intent in security-sensitive configuration.

## Implemented
- Added explicit allowlists for nested `security`, `daemon`, and `messaging` configuration keys.
- Require each MCP server to have a non-empty HTTPS endpoint; normalize trailing slashes for duplicate endpoint detection.
- Reject duplicate MCP endpoints, duplicate tool names, blank tool names, and malformed allowed-tools lists.
- Added regression tests for unknown nested keys, duplicate/blank MCP tools, and duplicate/empty endpoints.

## Verification
Code and tests were pushed to the feature branch. The previous CI pass applied to an earlier commit; run and verify CI against the exact new head before treating this phase as green.

## Remaining release gates
1. Define one canonical, versioned configuration schema shared by onboarding and runtime YAML; provide reversible migration and rollback.
2. Ensure all entry points use the same validated configuration service.
3. Add integration tests for actual MCP client startup and centralized authorization; configuration validation alone does not secure execution.
4. Run current-head CI, lint/type checks, dependency and secret scans, and human security review.
5. Keep PR #35 in draft until all release gates pass.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This phase reduces configuration ambiguity but does not establish full application or deployment readiness.