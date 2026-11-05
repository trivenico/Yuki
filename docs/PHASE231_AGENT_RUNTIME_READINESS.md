# Phase 231 — Agent runtime readiness policy

Introduces immutable provider/model/capability requirements and a fail-closed
readiness evaluator. A specialist is ready only when the selected provider and
model exactly match its declaration, the provider report is explicitly
live-verified, and every required capability is true.

Configuration and reachability are not live conformance. This module evaluates
evidence supplied by the provider runtime boundary; it does not construct a
provider or perform inference. Coordinator enforcement was added in Phase 232.
