# Phase 193 — evidence-bound external operation reconciliation

An uncertain provider operation can no longer be marked successful by supplying
only a provider reference. Resolution now requires:
- the exact dispatched provider as evidence source;
- a lowercase SHA-256 digest for externally retained lookup evidence;
- a provider reference and the current operation version;
- the existing task:operation:resolve authorization through TaskRuntime.

The evidence itself is not copied into the workflow database. The digest is an
integrity reference, not proof that the provider response is authentic. A trusted
provider adapter or authorized operator must independently perform and validate
the lookup before calling this transition. The operation journal records the
evidence source, digest and resolving principal in its durable outcome/audit.

This is not automatic retry and does not prove provider success on its own.
