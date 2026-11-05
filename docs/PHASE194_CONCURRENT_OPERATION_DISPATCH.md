# Phase 194 — concurrent operation dispatch guard

The operation journal already uses SQLite `BEGIN IMMEDIATE`, a version
precondition, a prepared-state check and a conditional update before recording
the dispatch transition. This phase adds a two-thread race regression test to
prove that concurrent dispatch attempts using the same operation version cannot
both win.

The test asserts exactly one in-flight transition and one durable dispatch
event. The losing caller receives a conflict/transition error. This protects
the journal's single-dispatch decision; provider-side idempotency keys are still
required because a process can fail after the provider accepts a request but
before Yuki records its result.
