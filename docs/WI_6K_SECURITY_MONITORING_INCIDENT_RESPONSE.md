# WI-6K — Security monitoring and incident response

SecurityEventMonitor accepts normalized signals for authorization denials,
administrator-authentication denials, workload credential verification
failures, signing-key revocation, and audit-integrity failures.

Signals require a stable unique ID, bounded reason code, and SHA-256 subject
hash for threshold grouping. Raw identities, tokens, prompts, request bodies,
key material, exception text, and arbitrary metadata are rejected. Default
detection is five matching denials within 300 seconds (both configurable within
bounded limits). Key revocation creates a high-severity incident immediately;
audit-integrity failure creates a critical incident immediately. Duplicate
signal IDs are idempotent.

Incident reads and acknowledge/resolve transitions require a host-owned
authorize_operator callback. Event rows and incident history are append-only.
The monitor detects and preserves evidence; it does not revoke credentials,
disable workloads, quarantine agents, or modify policy automatically.

A deployment should forward normalized incidents to its SIEM/alerting provider
through a separately managed worker. This change introduces no outbound
network access or provider credentials. SQLite is a single-host starter;
multi-host deployments need a shared transactional backend, reliable event
forwarding, external archival/retention, and monitored notification delivery.
Tests were added but not executed locally in this connector-only session.
