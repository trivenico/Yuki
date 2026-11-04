# Security policy and scope

Yuki is an early-stage project and is **not production-ready or independently security-certified**. Passing CI demonstrates only the code paths exercised by automated tests; it is not a substitute for deployment-specific security review or penetration testing.

## Intended security use

The planned cybersecurity track is defensive and authorization-based: review of user-provided code, dependency and secret detection, secure configuration checks, asset inventory within an approved scope, and remediation support.

## Guardrails

- Only assess systems and data the operator owns or is explicitly authorized to test.
- No stealth, persistence, credential theft, evasion, or unauthorized access features.
- Active scans, network requests, destructive changes, publication, and other consequential actions should require explicit scope and approval.
- Treat external documents, websites, source code, and tool outputs as untrusted input.
- Do not commit credentials, personal memory, task records, audit logs, or customer data.
- Yuki has configured OpenAI-compatible, Ollama, Gemini CLI, and hybrid provider adapters; actual availability depends on operator-supplied credentials, local runtimes, and configuration. Provider support does not imply production reliability or security certification.\n- Defensive security capabilities remain scoped and guarded. The repository does not claim a general-purpose production cybersecurity scanner.\n- The Docker task runner is a bounded local execution control, not a hardened multi-tenant isolation boundary or microVM.\n- The authenticated web chat is a local MVP. Public deployment still requires a trusted TLS edge, host/origin policy, login throttling, network policy, secret management, production database validation, and browser security testing.

## Reporting issues

Until a dedicated private security reporting process is established, do not publish secrets, access tokens, or sensitive exploit details in public issues. Use a private channel controlled by the repository owner when available.
