# DarkChocolate security model (initial vertical slice)

## Implemented
- Heuristic prompt inspection runs before model inference.
- A workflow proposal must be explicitly approved before the request reaches the model.
- Tool calls pass through a central registry and access/risk checks.
- HIGH and CRITICAL tool calls fail closed without explicit approval.
- The report writer is restricted to a plain filename inside the configured workspace.
- Audit events are appended locally; common key-value token/password patterns are redacted.

## Limitations
- Prompt scanning is heuristic and cannot prove a request safe.
- Face authentication, egress firewall, subprocess sandbox, MCP, browser, email, packet capture, voice, kill switch, and nightly evolution are not implemented.
- Only a workspace-scoped report writer is registered; no arbitrary shell or penetration-test tool is enabled.
- Planner currently creates a one-step proposal; it is not a structured task planner.
- Audit redaction is partial. Protect local data and review retention.
- This build does not implement cloud fallback.
- Knowledge graph is JSON-based; ChromaDB and semantic retrieval are not included.

Run on a trusted local account. Keep personal data, logs, and workspace files out of version control. Do not expose Ollama's HTTP port to untrusted networks.
