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
- The current prototype has no real cybersecurity scanner, no live model provider, and no production-grade sandbox.

## Reporting issues

Until a dedicated private security reporting process is established, do not publish secrets, access tokens, or sensitive exploit details in public issues. Use a private channel controlled by the repository owner when available.
