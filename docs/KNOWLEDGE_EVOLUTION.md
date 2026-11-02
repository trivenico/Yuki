# Knowledge Evolution Engine

\`KnowledgeEvolutionEngine\` turns proposed memory improvements into a
reviewable, version-checked workflow. It does not ingest task output or mutate
memory automatically.

## Workflow

1. An authenticated actor with \`evolution:propose\` access creates a proposal
   against a readable memory version.
2. The proposal records the expected source version, rationale and evidence
   reference.
3. A reviewer with \`evolution:review\` access approves or rejects it.
4. Approval requires the source memory version to remain unchanged and, by
   default, a reviewer different from the proposer.
5. Approved content is written through the vault's normal write/update path,
   creating a new version and audit event.

## Example

\`\`\`python
from memory import KnowledgeEvolutionEngine

engine = KnowledgeEvolutionEngine(
    vault,
    authorize=proposal_authorization_policy,
    require_independent_reviewer=True,
)
proposal = engine.propose(
    actor_id=authenticated_agent,
    memory_id=existing_memory_id,
    proposed_content="Revised and evidence-backed knowledge.",
    rationale="A completed task found a missing operational constraint.",
    evidence_ref="workflow://completed-task/123",
)
# A separately authenticated human/reviewer approves:
engine.review(
    reviewer_id=authenticated_reviewer,
    proposal_id=proposal.proposal_id,
    decision="approve",
)
\`\`\`

## Authorization

Provide a trusted \`authorize(actor_id, action, resource) -> bool\` callback.
Actions are \`evolution:propose\`, \`evolution:read\`, and \`evolution:review\`.
Resource identifiers use \`<scope>:<scope_id>\`. Callback errors fail closed.
The vault independently enforces its own read/write/update permissions.

## Reliability and operational boundaries

- Proposal status is persisted in SQLite. Approval uses a short-lived
  \`reviewing\` claim so no proposal-store transaction is held while updating
  the vault. A process crash during approval can leave a proposal in
  \`reviewing\`; operators must inspect the source memory version and reconcile
  it explicitly rather than blindly retrying.
- The source version is checked immediately before writing. Concurrent source
  updates cause the proposal to fail as stale.
- The proposal store and vault may share a SQLite file by default. The
  approval operation spans two SQLite connections and is not a single atomic
  transaction; use the documented reconciliation procedure after process or
  disk failure.
- Proposal evidence references are metadata, not proof. Verify the referenced
  workflow artifact and its integrity before approval.
- Approved content remains untrusted data. It must never grant tools,
  permissions, or executable instructions.
- No model calls, automatic ingestion, external provider, or auto-approval is
  included.

## Tests

\`\`\`bash
python -m unittest tests.test_knowledge_evolution
\`\`\`
