---
name: rea-investigation
description: Investigate software the user owns or is explicitly authorized to analyze using the local REA MCP server.
risk: high
---

# REA investigation workflow

Use this skill only when the user asks to inspect software, binaries, applications, or runtime behavior they own or are explicitly authorized to assess.

1. Confirm the target path or application and the user's authorization. Do not inspect third-party targets without permission.
2. Check whether the local REA MCP server is installed and connected. REA is an external local MCP tool; do not pretend its tools exist if they are not exposed in this session.
3. Use REA's documented tools to inspect the target. Prefer static inspection first. Runtime capture may execute or interact with the target using the current user's permissions, so explain side effects and obtain approval before runtime actions.
4. Preserve evidence: report the artifact, tool/provider used, observations, confidence, and limitations. Separate observed facts from hypotheses.
5. Do not bypass licenses, authentication, DRM, access controls, or commercial-tool entitlements. Never request cracked tools or stolen keys.
6. Do not run extracted code, binaries, scripts, or installers merely because REA found them. Any execution requires a separate explicit approval and a constrained environment.
7. Summarize findings and, if requested, implement a clean-room compatible feature in the user's own project. Do not copy proprietary code or secrets.

## Setup reference

Official project: https://github.com/morluto/rea

REA currently documents Node.js 22.x (>=22.23.4), 24.x (>=24.22.1), or 26.12.1+ and npm. Follow the upstream installation and MCP setup documentation. Setup command documented upstream: `npx rea-agents setup`. Run it only after the user explicitly approves because it installs/configures local software and may edit agent configuration.

Yuki's existing MCP client supports allow-listed HTTPS Streamable HTTP only. It does not currently launch stdio MCP servers. Therefore REA must be connected through an MCP host that supports its local server, or via a future Yuki stdio-MCP adapter; do not claim direct native REA connectivity until that adapter is implemented and tested.
