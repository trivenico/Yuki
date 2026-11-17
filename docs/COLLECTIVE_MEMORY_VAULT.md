# Yuki Collective Memory Vault

The \`memory.collective_vault.CollectiveMemoryVault\` adds a portable, local-first
memory layer alongside the existing \`companion.MemoryStore\`,
\`core.VectorMemory\`, and \`knowledge.KnowledgeGraph\`. It does not silently
replace or migrate those stores.

## Quick start

\`\`\`python
from memory import CollectiveMemoryVault

vault = CollectiveMemoryVault(
    "YukiData/collective-memory.sqlite3",
    authorize=authorization_policy,
)

record = vault.remember(
    actor_id=authenticated_actor,
    scope="project",
    scope_id="yuki",
    category="decision",
    content="Keep task state in the durable workflow runtime.",
    source_type="architecture_decision",
    source_ref="docs/architecture/ADR-001.md",
)
matches = vault.search(
    actor_id=authenticated_actor,
    query="durable workflow task state",
    scopes=(("project", "yuki"),),
)
\`\`\`

## Data model

- Scope: \`private\`, \`project\`, \`team\`, or \`global\`.
- Category: \`decision\`, \`fact\`, \`lesson\`, \`pattern\`, \`handoff\`, or
  \`preference\`.
- Provenance: source type, source reference, and SHA-256 content digest (unless
  a trusted upstream digest is supplied).
- Version history: every update appends a version record.
- Audit: create, update, and delete events record actor and timestamp.
- Search: bounded lexical retrieval restricted to explicitly supplied scopes
  and ACL-approved reads.

## Authorization contract

The application must provide a trusted callback with the signature
\`authorize(actor_id, action, resource) -> bool\`. It must authenticate the
actor and apply policy for the requested resource. Callback exceptions fail
closed. Resources are represented as \`<scope>:<scope_id>\`.

Actions include \`memory:write\`, \`memory:update\`, \`memory:read\`,
\`memory:history\`, \`memory:delete\`, and \`memory:export\`. Imports are
performed through normal write authorization. Do not use a permissive callback
in a production deployment.

## Portability and backups

Use \`export_records\` to produce a schema-versioned JSON export for explicitly
authorized scopes. Transfer it over a trusted channel and use
\`import_records\` on the destination device. The export is a portable data
format, not an encrypted backup. Protect it like the source database.

The SQLite file belongs under the user data directory (for example
\`YukiData/\`), not in Git. Commit schemas, migration code, tests, and
documentation; never commit private memory contents, secrets, or credentials.
Use the existing daily backup scripts for local data, and test restore on a
separate device/directory before depending on it.

## Safety and limitations

- Memory is untrusted data, never an instruction or tool authorization.
- No model calls, embeddings, network access, or automatic memory ingestion.
- Search is lexical, not semantic/vector search.
- Deleted records are soft-deleted; audit and historical versions remain in
  the database. This is not cryptographic erasure.
- The application must define retention, encryption-at-rest, backup
  encryption, data residency, and cross-device conflict policy.
- A supplied source digest is trusted metadata; verify it upstream against an
  immutable source artifact before relying on it.
- The initial import creates new destination records; it does not preserve
  source record identifiers or merge histories.
- Review privacy and consent before sharing project/team/global scopes.

## Tests

\`\`\`bash
python -m unittest tests.test_collective_memory_vault
\`\`\`
