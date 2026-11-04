# Agent experience-to-review pipeline

`AgentLearningPipeline` connects the existing deterministic task evaluator
with the owner-scoped evaluation review queue.

## Flow

1. A completed task and independently verified signals are supplied by a
   trusted execution/evaluation integration.
2. The evaluator applies its fixed weighted rubric and evidence sufficiency
   rules.
3. The report is persisted in the actor-scoped review queue.
4. The owner reviews the report and its evidence references.
5. A future, separately authorized knowledge-promotion workflow may turn an
   approved report plus a deliberately authored knowledge candidate into
   durable memory.

## Safety boundary

The pipeline does not treat an agent's own output as proof. The caller must
supply evidence references to independently checked artifacts, test results,
or trusted observations. The pipeline never writes memory, changes routing,
updates models, or trains weights. An approved evaluation is not itself a
memory write authorization.

## Example

```python
pipeline = AgentLearningPipeline(evaluator, review_queue)
result = pipeline.evaluate_completed_task(
    actor_id="owner",
    task_id="task-101",
    agent_id="research",
    outcome="succeeded",
    independently_verified_signals={
        "correctness": 1.0,
        "evidence_quality": 0.9,
        "instruction_compliance": 1.0,
        "completeness": 0.9,
        "efficiency": 0.8,
    },
    evidence_refs=["test-run:101", "artifact:101"],
)
```
