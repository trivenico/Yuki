# Yuki AI application service

Yuki AI is the shared intelligence service used by every interface. CLI and native desktop call the same application service rather than implementing separate prompting, planning or memory behavior.

## Current service surface

- `YukiAI.answer(message)`: provider-independent chat with bounded optional memory context.
- `YukiAI.plan(goal)`: bounded inspectable plan with optional memory context.
- `YukiAI.run_task(goal)`: delegates only when an approval-gated task agent was explicitly configured.
- `YukiAI.status()`: truthful runtime readiness and capability report.

Memory adapters are injected; retrieval and persistence failures are non-fatal. Retrieved memory is treated as untrusted reference data. The service never grants tool permissions. Existing skill execution and human approval gates remain authoritative.

## CLI and desktop

The CLI constructs this service from its configured provider and exposes `ai-status`, `plan`, `ask` and `chat`. Desktop imports the same `YukiAI` class through `interfaces.desktop.model_api`. Neither interface should duplicate provider routing or planner policy.

## Not claimed as complete

This is an application-level orchestration facade, not a newly trained foundation model. Its intelligence depends on the selected pretrained provider/model. Persistent cross-session memory adapters, production streaming, native desktop shell and model lifecycle transactions remain separate deliverables.
