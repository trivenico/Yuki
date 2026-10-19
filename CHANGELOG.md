# Changelog

All notable changes to the Yuki project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-19

### Added
- Standard setuptools `pyproject.toml` configuration and `yuki` console entry point.
- Conversational multi-turn chat loop via `/api/chat` with persona system instructions, local memory context injection, and rolling 12-turn message history.
- Automatic stdlib `.env` file loader in `core/config.py`.
- Comprehensive subsystem readiness diagnostics module in `scripts/doctor.py` and CLI `/doctor` command.
- Rotating file logging in `workspace/logs/yuki.log` and append-only JSONL approval audit trail in `workspace/approval_audit.jsonl`.
- `MCPToolSkill` in `skills/mcp_skill.py` and CLI `/mcp` discovery and execution commands in `main.py`.

### Fixed
- Fixed crash when invoking `/skill ` with trailing whitespace or missing skill name.
- Added top-level command dispatch exception guards in `main.py` REPL loop to prevent unhandled command errors from terminating the session.
- Fixed provider selection type validation in `providers/factory.py`.
- Fixed `DeviceScannerSkill` constructor workspace signature mismatch.
