# Phase 10 — Guarded runtime integration audit

## Implemented
- `main.py` now exposes an opt-in `/react GOAL` command backed by the native OpenAI Chat Completions tool-call adapter and bounded ReAct loop.
- ReAct tools are constructed only from the existing skill registry. Each tool handler delegates to `YukiCoreAgent.run_skill`, preserving the established per-invocation permission gate. Raw shell and raw MCP handlers are not registered.
- ReAct conversation history now preserves the provider assistant tool-call message before the corresponding tool observations.
- Added a regression assertion for tool-call history ordering.

## Explicit provider configuration
Set `YUKI_REACT_MODEL` and `OPENAI_API_KEY` before using `/react`. Optional variables are `YUKI_REACT_API_KEY_ENV` and `YUKI_REACT_BASE_URL`. Loopback HTTP is disabled unless `YUKI_REACT_ALLOW_LOOPBACK_HTTP=true`; HTTPS is the default. No automatic cloud fallback is enabled.

## Remaining architecture gaps
1. Existing production runtime configuration is YAML-based (`core/config.py`); the separate `~/.yuki/config.json` onboarding wizard is not yet the single source of truth. Do not enable automatic onboarding until a schema migration/compatibility layer and backup path are implemented.
2. `/react` currently uses the OpenAI Chat Completions tool schema. Anthropic-native tool calling, Ollama-native tool calling, provider health/circuit-breaker routing, and token/cost accounting remain open.
3. ReAct uses registered legacy skills, and each skill invocation still requires explicit human approval. This is intentional; no micro-action bypass is enabled.
4. MCP is not yet mounted as a tool provider in the ReAct registry. It must be connected only through the same permission gate and explicit server/tool allowlists.
5. Approved declarative `SKILL.md` files are staged/persisted but not yet compiled into the legacy Python registry. They remain data, not executable plugins.
6. Messaging HMAC helpers are not complete platform integrations. Desktop wrapper and supervised always-on service are not wired into the same backend lifecycle yet.
7. Run current-head CI and security review before merge; an older successful run does not validate these commits.

## Release gate
Keep PR #35 in draft until current-head CI passes, the onboarding/config compatibility story is resolved, and runtime/MCP authorization integration is reviewed. Do not run generated Python, allow arbitrary shell tools, or route to cloud implicitly.
