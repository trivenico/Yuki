# Phase 199 — provider readiness diagnostics

Added `providers/provider_readiness.py` to classify the explicitly configured
real provider without sending an inference prompt or selecting a fallback.

States are `not_configured`, `configured_unverified`,
`reachable_unverified`, `unavailable`, `partially_verified`,
`live_check_failed`, and `live_verified`. Reachability is not treated as
proof of model inference. Only a matching, explicit live-conformance report
with status `pass` can produce `live_verified`.

Mock configuration and mock provider instances are rejected. Diagnostics
never enumerate credentials, print endpoints, or trigger cloud routing. This
module reports readiness; it does not yet replace every existing application
provider-selection call site. A subsequent integration phase will wire this
diagnostic into application startup and agent routing.
