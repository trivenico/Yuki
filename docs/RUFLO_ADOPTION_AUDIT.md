# Ruflo-inspired integration audit

Reviewed public upstream repository: https://github.com/ruvnet/ruflo
Yuki review date: 2026-10-17

## Design ideas worth adapting

- Discoverable skill packages and a capability inventory.
- Observable model usage per session, including usage metadata from providers.
- MCP tools that are discoverable, schema-described, explicitly allow-listed, and executed through the existing permission gate.
- Clear separation between an agent harness, model provider, skills, workflows, and durable memory.
- Documentation that labels a capability as implemented, partial, scaffold, or planned.

These are design inspirations, not proof that every upstream claim is production-ready. Yuki should not copy unneeded framework complexity or imply that the presence of a module makes an integration operational.

## Yuki status after this phase

### Added
- `skills/discovery.py`: a Python package scanner that can find locally defined `Skill` subclasses and report import failures. Importing a discovered Python module executes its code with the process's permissions. The loader is not a sandbox and is not yet wired into the default registry; do not treat arbitrary dropped-in files as trusted.
- `core/token_usage.py`: local session-level token accounting, persisted event history, explicit exact-vs-estimated counts, and threshold/spike alerts.
- `tests/test_token_usage.py`: unit tests for counting, persistence, validation and alerts.
- `core/capabilities.py`: JSON-safe export of the current registry's capability descriptions for a future dashboard.

### Still partial / not connected end to end
- **Plugin auto-registration:** scanner exists, but boot-time registry integration and a trusted-plugin policy are not complete. Built-in registry remains the source of registered skills.
- **Capabilities dashboard:** inventory export exists. There is no web dashboard; current optional dashboard is Textual. The current registry descriptions may not expose meaningful per-skill parameter schemas unless a skill defines `input_schema`.
- **Ollama token usage:** tracker exists, but Yuki's provider interface currently returns plain text and the selected provider is the mock adapter by default. Exact Ollama usage requires a real Ollama adapter that captures the response's token metadata and passes it to the tracker. No actual Ollama usage is being monitored yet.
- **MCP execution:** existing `core/mcp_client.py` can list and call tools with host/tool allow-lists and per-call approval. It is not connected to runtime commands, registry discovery, or a capabilities UI. No automatic execution of newly discovered remote tools is enabled.
- **Autonomous agent:** there is no complete model-driven tool-selection loop. Discovery and accounting alone do not create autonomous execution.

## Security requirements before enabling plugin auto-discovery

1. Make discovery explicitly configurable and off by default until a trusted-plugin policy is in place.
2. Allow-list plugin module names or require a reviewed manifest and explicit trust confirmation.
3. Never assume Python import is sandboxed. For third-party/untrusted plugins, use a separate process/container with restricted filesystem, network, credentials and resource limits.
4. Keep a plugin inventory (name, version, source hash, declared permissions, schemas, approval policy).
5. Reject duplicate skill names, invalid schemas, unsafe permission declarations and failed imports without crashing core startup.
6. Require approval for consequential actions even when a skill comes from a trusted plugin.
7. Treat MCP descriptions, schemas and results as untrusted input; tool discovery must never imply permission to call the tool.

## Verification

GitHub Actions runs on this phase's commits have reported failures. The available workflow-log endpoint returned a missing artifact, so the exact failing assertion could not be confirmed. The new token tests are committed but must not be reported as passing until CI is green. The latest result is available at https://github.com/trivenico/Yuki/actions.
