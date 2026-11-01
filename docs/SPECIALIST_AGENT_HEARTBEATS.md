# Specialist agent liveness and routing

The specialist registry supports optional instance heartbeats. A heartbeat is
authorized through the dedicated `agent:heartbeat` action and is bound to a
registered, enabled logical agent plus a bounded instance identifier.

`heartbeat_agent` defaults to a 90-second TTL and accepts 15–3600 seconds.
The latest heartbeat is persisted in SQLite. Once an agent has opted into
heartbeat management, an expired heartbeat excludes it from new plans. Agents
that have never registered a heartbeat remain static registry entries for
backward compatibility and local/test deployments.

This is a liveness signal, not proof that a worker is healthy, secure, or
currently able to complete work. It does not terminate in-flight work, revoke
a task lease, or replace the durable task runtime's lease/heartbeat mechanism.
Workers must continue renewing their task leases independently.

Operational guidance:
- Use a unique instance identifier for each logical agent process.
- Renew well before TTL expiry; use jitter in the hosting service to avoid
  synchronized heartbeat bursts.
- Keep the heartbeat authorization separate from agent registration and plan
  dispatch permissions.
- Alert on heartbeat expiry and sustained worker failure; do not automatically
  grant permissions or retry side effects as a liveness response.
- Heartbeat timestamps use the host UTC clock. Production deployments should
  maintain clock synchronization.
