# Phase 230 — enforce contracts at agent execution boundaries

- Added optional, version-bound AgentContract injection to SpecialistAgentCoordinator.
- Contract registration is restricted to known agents and requires exact equality with the registered agent version.
- Validates the complete task argument (including bounded dependency context) immediately before enqueue; invalid input blocks dispatch and records only issue codes and contract version.
- Validates worker output before marking the specialist subtask succeeded; invalid output is rejected from the plan and recorded as a failed subtask.
- Preserves compatibility for existing agents without a registered contract. This is an explicit migration mode, not proof that every agent is contract-protected.

Limitations: contract definitions are supplied to the coordinator at construction and are not yet persisted in the registry database. Existing tasks already enqueued before contract registration cannot be retroactively protected. The underlying worker task result remains unchanged for audit/reconciliation; the coordinator refuses to accept it as a successful specialist result. Provider/model live readiness and resource enforcement remain separate work.
