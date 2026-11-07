# Phase 189 — Strict agent dependency message schema

Dependency handoffs now declare schema version 1 and pass through one bounded serializer before being attached to downstream tasks.

Validation requires bounded subtask and specialist identifiers, a non-empty task ID of at most 200 characters without control characters, text content without NUL bytes, and unique source subtask IDs. Content is capped at 500 UTF-8 bytes without splitting a code point. Only fixed envelope/result fields are emitted. The complete envelope must fit the remaining 2,000-byte task argument budget; entries that do not fit are skipped whole.

This validates shape and provenance consistency; it does not cryptographically authenticate workers or make their output trustworthy. Downstream agents must continue treating the envelope as untrusted reference data and enforce permissions independently.

Tests cover malformed identifiers, empty task IDs, NUL content, duplicate sources, schema version, and byte-budget behavior.
