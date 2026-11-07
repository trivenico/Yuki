# Phase 190 — Durable privacy-preserving agent handoff audit

Each dependency result passed to a downstream specialist now has a durable audit record linking the plan, source subtask, destination subtask, source specialist and source runtime task.

The audit stores only SHA-256 of the exact bounded content included in the handoff and its UTF-8 byte length. It does not duplicate message content. A uniqueness constraint prevents duplicate records for repeated advancement of the same plan edge.

The `handoff_audit` reader requires the existing `agent:read` authorization and bounds pages to 1–500 records. Records are returned in insertion order and survive coordinator restart.

Tests cover provenance, content exclusion, restart durability, authorization and page bounds. The serializer's NUL check now rejects the actual NUL character rather than the escaped text sequence.
