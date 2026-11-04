# Agent experience-to-review pipeline

`AgentLearningPipeline` connects the deterministic evaluator to the
owner-scoped review queue.

1. A trusted caller supplies a completed task, independently verified signals,
   and references to checked artifacts or test results.
2. The evaluator applies its versioned rubric and evidence sufficiency rules.
3. The report is persisted in the actor-scoped human review queue.
4. The owner reviews the report and its evidence.
5. A later, separately authorized knowledge-promotion operation may create a
   durable memory candidate.

The pipeline never writes memory, changes routing, updates model weights, or
executes tools. An approved evaluation is not itself authorization to write
memory. Agent self-reports are not independent evidence.
