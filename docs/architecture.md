# Yuki Architecture & Module Map

Yuki is a desktop/terminal AI personal assistant built with Python 3.10+ and standard library primitives.

## System Subsystems

- **Core (`core/`)**:
  - `agent.py`: YukiCoreAgent - Bounded skill execution and permission gate.
  - `permissions.py`: PermissionGate - Human-in-the-loop approval gate with rate limiting.
  - `config.py`: Configuration and stdlib `.env` file loader.
  - `env_guard.py`: Environment variable scrubbing prior to subprocess execution.
  - `skills_guard.py`: Source code scanner for dangerous patterns in plugins.
  - `token_usage.py`: Token usage tracking and threshold alerts.
  - `mcp_client.py`: Restricted HTTPS MCP HTTP client.

- **Companion (`companion/`)**:
  - `memory.py`: MemoryStore - Atomic file-backed JSON long-term memory.
  - `proactive.py`: ReminderQueue and ReminderSchedulerThread background thread.
  - `heartbeat.py`: Read-only proactive HEARTBEAT.md checker.
  - `evolution.py`: Daily evolution review cycle.

- **Skills (`skills/`)**:
  - `registry.py`: SkillRegistry - Central registry for registered skills.
  - `mcp_skill.py`: MCPToolSkill wrapper for approved MCP HTTP tool execution.

- **Providers (`providers/`)**:
  - `factory.py`: Provider factory supporting `mock`, `ollama`, and `hybrid` modes.
  - `ollama.py`: Local Ollama provider supporting `/api/generate` and `/api/chat`.
  - `hybrid.py`: Task-routed hybrid local/cloud provider.
