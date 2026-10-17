# Security Lab Engine (planned)

This folder replaces the unsafe-sounding "hackback" label with a scoped security-lab boundary.

## Current status
No scanner, exploit runner, OSINT collector, or remote-target execution engine is implemented here. Nothing in this directory is automatically executed by Yuki.

## Design requirements before implementation
- Only user-owned systems, explicitly authorized assessments, or intentionally vulnerable local labs.
- Require a clearly specified target scope, purpose, and expiry before any scan.
- Default-deny, narrow allow-list, explicit approval for each consequential action, cancellation, timeouts, output limits, and metadata-only audit logs.
- Never implement hack-back, retaliation, persistence, credential theft, or unrestricted exploitation.
- Prefer simulated targets and local lab networks. Containerization alone is not a security guarantee.
