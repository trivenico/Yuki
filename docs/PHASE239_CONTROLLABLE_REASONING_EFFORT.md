# Phase 239 — Controllable Reasoning Effort

**Status:** Bounded effort policy implemented; CI verification pending.

## Inkling lessons adopted

Inkling exposes controllable thinking effort, supports agentic tool use, multimodal input and long-horizon work. Its self-fine-tuning demonstration also illustrates a complete improvement loop: define an objective, prepare data and evaluation, fine-tune, compare against the base model and switch only after evaluation.

Yuki adopts the engineering patterns, not an assumption that the Inkling weights fit ordinary laptops.

## Existing Yuki foundations

- Owner-scoped reasoning runtime with explicit local-only default and cloud opt-in.
- Agent execution loop with model-turn, tool-call, duration and transcript ceilings.
- Deterministic evidence-based agent evaluator.
- Provider-neutral candidate model evaluator.
- Model artifact/catalog provenance and approval controls.
- Local hardware snapshot and conservative CPU-first resource profiles.

## New policy module

`core/reasoning/effort_policy.py` introduces four bounded profiles:
- `low`: simple tasks and lower-resource devices.
- `standard`: everyday multi-step assistance.
- `high`: research, coding and engineering work.
- `maximum`: explicitly authorized, bounded long-running reviews.

Task classes have conservative defaults. Explicit effort requests are allow-listed. Device limits can only reduce budgets; they cannot expand policy ceilings. The result maps directly to the existing AgentExecutionLoop execution options.

This is a resource-budget policy, not a claim that the model internally reasons better or uses a particular number of hidden tokens. It grants no tools or permissions.

## Next integration

Wire the policy into the authenticated task router and owner-scoped reasoning runtime, using task classification and the local device execution profile. Preserve independent hard limits at the execution loop and worker service. Record selected effort, applied limits, actual usage and outcome in task telemetry.

## Long-horizon engineering evaluation

Add benchmark suites that assess entire workflows, not only response strings:
- repository task completion and regression safety;
- tool selection and permission compliance;
- evidence quality and source-grounded conclusions;
- recovery after tool failures;
- context compaction and task resumption;
- wall-clock, model-turn and tool-call efficiency;
- human review of ambiguous or high-impact outcomes.

Do not let an LLM grade itself as the sole promotion authority. Use deterministic checks, independent graders, held-out tasks and human review for consequential changes.

## Multimodal path

Provider capability declarations should gate image/audio tasks. A request must not silently degrade to text-only while claiming multimodal understanding. Local GPU/VRAM and runtime compatibility must be verified before admitting large multimodal weights.

## Not delivered in this phase

No Inkling weights are downloaded or bundled. No Tinker account/API is configured. No fine-tuning job is launched. No active model is switched. The effort policy module is ready for controlled integration, but the runtime/task router wiring is a subsequent phase.
