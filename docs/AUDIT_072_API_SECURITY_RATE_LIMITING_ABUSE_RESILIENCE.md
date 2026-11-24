# Audit 072 — API Security, Rate Limiting, Abuse Prevention & Denial-of-Service Resilience

**Scope:** ASGI entry points, authentication parsing, request body bounds, login abuse controls, expensive operations, concurrency, timeouts, and deployment-level traffic controls.

**Reviewed source:** api/web.py, api/runtime.py, api/conversations.py, api/inference.py, api/evaluations.py, api/income.py, api/mcp_management.py, api/oauth_connections.py, api/oauth_authorization.py, core/auth.py, core/agent_execution_loop.py, core/config.py, and related API tests including tests/test_conversation_api.py, tests/test_api_runtime.py, and tests/test_oauth_connections_api.py.

## Positive controls observed

- Main conversation API bounds JSON request bodies to 24,000 bytes and validates JSON object shape. Inference and OAuth authorization APIs also impose bounded request-body readers.
- Login returns generic invalid-credential errors. Auth service performs dummy PBKDF2 work for unknown users and supports per-account failed-login lockout (default five failures and 300 seconds).
- Conversation and agent execution inputs have bounded lengths/counts; agent loop has model-turn, tool-call, duration, transcript, and cooperative cancellation limits.
- Conversation generation has owner-scoped cancellation and active-generation tracking; durable generation leases and optimistic concurrency protect persisted writes.
- API responses generally use no-store and nosniff headers; web shell adds CSP, frame denial, referrer policy, and content-type protections.
- OAuth connection and authorization routes authenticate session tokens and use owner-scoped registries. Duplicate Authorization header rejection already exists in OAuth authorization API.

## Findings

### P1 — Primary conversation API accepts ambiguous duplicate Authorization headers (fixed in companion PR)

The primary YukiConversationAPI._principal implementation converted all headers into a dictionary. Repeated Authorization fields were silently collapsed, selecting one value according to ordering. Proxies, ASGI servers and application layers may differ in how they combine duplicate fields, creating a credential interpretation discrepancy.

**Fix submitted:** collect Authorization fields explicitly and require exactly one. Missing or duplicate values fail closed as unauthenticated. Added regression coverage asserting duplicate Authorization headers return 401.

### P1 — No application-level distributed rate limit or global concurrency admission control demonstrated

The reviewed ASGI runtime and API wrappers do not implement a shared per-IP, per-account, per-session, or per-route rate limiter, nor a distributed concurrency budget. Account lockout limits repeated failures against an individual known account, but it is not a general request-rate limit and does not protect expensive authenticated routes such as inference, evaluations, MCP discovery, OAuth operations, or conversation generation.

**Impact:** a single client or coordinated clients may consume worker slots, database connections, model-provider quota, thread-pool capacity, or downstream service capacity. Per-account lockout alone does not prevent distributed credential stuffing or resource exhaustion across many accounts/IPs.

**Recommendation:** deploy edge/WAF rate limits and request concurrency controls, then add shared application-level limits keyed by trusted client IP plus authenticated principal and route cost class. Use a distributed store for multi-worker deployments; define fail-open/fail-closed behavior, bounded key cardinality, trusted proxy configuration, retry-after responses, and separate budgets for login, generation, inference, OAuth, and read endpoints. Never trust arbitrary forwarded-IP headers unless the immediate proxy is configured as trusted.

### P1 — ASGI body-size limits do not establish connection/header/slow-client limits

Application body readers cap accumulated request bytes, which is useful after the server has delivered body events. The reviewed app does not configure server-level header size, header count, keep-alive, request read timeout, slowloris protection, connection cap, or maximum in-flight request settings.

**Impact:** these protections depend on the ASGI server, reverse proxy, ingress controller, and hosting platform. Application-level body caps alone do not stop clients from holding connections open slowly or sending oversized headers before application dispatch.

**Recommendation:** publish a supported deployment profile with explicit server/proxy limits for header bytes/count, request body bytes, read/idle timeout, keep-alive, connection count, worker count, and upstream queue length. Validate those limits with load and slow-client tests.

### P2 — Blocking operations are moved to worker threads but not uniformly bounded by request deadlines

Several routes use asyncio.to_thread for password verification, persistence, provider/service operations, and inference work. The agent loop has a cooperative deadline, but the reviewed API composition does not demonstrate one end-to-end deadline budget spanning queue wait, database acquisition, model/provider call, tool execution, and response delivery for every route.

**Impact:** cancellation of an awaiting coroutine does not necessarily stop the underlying thread or blocking operation. Saturation may outlive client disconnects and continue consuming resources.

**Recommendation:** define per-route deadline budgets and bounded executor/worker pools; propagate cancellation into adapters that support it; set database/provider timeouts; cap queued work; and report timed-out-but-still-running tasks distinctly.

### P2 — Operational abuse telemetry and automated load-shedding policy are not demonstrated

The inspected API paths do not show a unified set of metrics for request rates, auth failures, 429s, active generations, queue depth, executor saturation, provider latency, rejected body sizes, or per-principal resource consumption.

**Recommendation:** emit structured, privacy-safe counters/histograms and alerts; avoid logging credentials or raw request bodies. Define overload responses and load shedding for expensive optional routes before critical authentication and cancellation paths are starved.

## Remediation status

- **Implemented in companion PR #1131:** duplicate Authorization header rejection in the main conversation API, with regression test.
- No rate limiter or global concurrency middleware was added. A process-local limiter would be bypassed by multiple workers/restarts and could create false assurance; a production-grade implementation requires trusted proxy topology, distributed state, policy thresholds, and operational configuration.
- Server/proxy connection controls and load tests remain deployment-level work.

## Validation status

- Source and selected test inspection completed.
- Regression test added but **not executed**.
- No live ASGI server, reverse proxy/WAF, multi-worker deployment, load test, slow-client test, or distributed limiter was available/performed.
- PRs are submitted but not merged or deployed.
