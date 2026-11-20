# Nuclei-inspired vulnerability assessment integration

## Initial implementation: report import only

Yuki's first Nuclei integration is an offline JSONL report importer. It does not install, launch, or wrap the Nuclei executable, download templates, resolve DNS, or contact targets.

The importer:
- Accepts immutable UTF-8 bytes and enforces report, line, and finding-count limits.
- Rejects malformed JSON, duplicate keys, invalid required fields, and oversized records.
- Retains bounded finding metadata: template ID, name, severity, protocol, reported target, matcher name, timestamp, and the count (not values) of extracted results.
- Removes URL userinfo, query strings, and fragments from matched locations.
- Never stores raw request/response fields or extracted-result values.
- Labels every record as an unverified imported report; it is not proof that a vulnerability exists.
- Does not authorize remediation, active validation, or changes.

## Future template and execution policy

Nuclei templates are untrusted content. A future integration must not execute a template merely because it parses or appears in an official repository. Before any active checks are considered, Yuki must implement:

1. Immutable template provenance: pinned source/revision, content digest, signer/repository verification, and review status.
2. Static template policy: explicit protocol and method allow-lists; reject code, headless/browser actions, workflows, payload/fuzzing, raw requests, interactsh/OAST callbacks, file/network side effects, and destructive matchers.
3. Exact owner-approved target scope with exclusions, expiry, DNS-rebinding safeguards, redirect policy, and no scope expansion.
4. A verified isolated runtime with no host secrets, no host mounts, no unrestricted egress, bounded concurrency/rate/time/output, and tested cancellation.
5. Visible session state, audit events, evidence redaction, retention controls, and a human-reviewed report.
6. A dedicated adapter with typed command construction; never concatenate model output into shell commands.

Even a statically reviewed HTTP GET template is an active network request. Therefore report import remains the only implemented mode; template execution and live scanning remain disabled until every gate is independently verified. No exploit delivery, credential attacks, persistence, stealth, destructive checks, or post-exploitation are in scope.
