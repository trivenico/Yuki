# Security policy and scope

Yuki is an early-stage project. It is not a certified security product and must not be treated as a substitute for professional security review.

## Intended security use

The planned cybersecurity track is defensive and authorization-based: review of user-provided code, dependency and secret detection, secure configuration checks, asset inventory within an approved scope, and remediation support.

## Guardrails

- Only assess systems and data the operator owns or is explicitly authorized to test.
- No stealth, persistence, credential theft, evasion, or unauthorized access features.
- Active scans, network requests, destructive changes, publication, and other consequential actions should require explicit scope and approval.
- Treat external documents, websites, source code, and tool outputs as untrusted input.
- Do not commit credentials, personal memory, task records, audit logs, or customer data.
- The current prototype has no production-grade cybersecurity scanner or production-grade sandbox. It contains Ollama and OpenAI-compatible text-generation adapters and a bounded JSON-based task loop, but the default configuration selects a mock provider. Real-provider connectivity, tool-call quality, timeout behavior, and end-to-end safety are not certified by the presence of these modules; use the explicit readiness status in README and the production roadmap.

## Reporting issues

Repository owners should enable GitHub Private Vulnerability Reporting (or publish a verified private security contact) before inviting external security testing. Until that is confirmed, do not publish secrets, access tokens, personal data, or sensitive exploit details in public issues. The presence of this file does not mean a private reporting channel is configured.
