# Phase 188 — Agent dependency handoff provenance

## Change

Dependency results passed from completed specialist subtasks to downstream tasks now use a compact JSON envelope instead of ambiguous bracket-prefixed prose.

Each result records its source subtask ID, registered specialist agent ID, runtime task ID, and content. The envelope explicitly marks result content as untrusted data.

## Bounds and behavior

- The downstream task description and serialized envelope remain within the existing 2,000-byte argument budget.
- Each source result is capped at 500 UTF-8 bytes; truncation avoids invalid UTF-8.
- A result that cannot fit as a complete JSON entry is skipped rather than producing malformed JSON.
- Only successful dependency task outputs are eligible for handoff.
- Result content never becomes permissions or executable instructions.

## Security boundary

This is provenance metadata and prompt-injection resistance, not cryptographic attestation. Source IDs identify coordinator/runtime records; they do not prove worker executable code or model behavior. Downstream workers must treat the entire envelope as untrusted input and enforce their own tool permissions.

## Validation

The dependency handoff test checks JSON parsing, trust-boundary labeling, and exact source subtask/agent/task attribution.
