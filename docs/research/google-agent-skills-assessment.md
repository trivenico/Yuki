# Google Agent Skills: research and Yuki adoption assessment

**Reviewed:** 2026-11-04  
**Upstream:** https://github.com/google/skills  
**Reviewed upstream revision:** `496fbede89dac1faf0d180a936bfc472b6fbf886`  
**License:** Apache License 2.0 (retain the license and required notices when copying or modifying upstream material).

## Scope clarification

There are two different Google destinations:

- **Google Agent Skills repository** (`google/skills`): reusable agent-oriented technical instructions, reference files, scripts, and assets.
- **Google Skills** (`skills.google` / `skills.google/paths`): learning paths and training content, not the same thing as the GitHub skill catalogue.

Yuki should use the GitHub repository as a curated source of engineering knowledge and optionally link to Google Skills learning paths for human learning. Neither should become a mandatory runtime dependency.

## How upstream skills are structured

The repository README groups skills by product/domain and documents selective installation with `npx skills add google/skills`. The individual skill entry point is `SKILL.md`, commonly with YAML frontmatter:

```yaml
---
name: agent-platform-eval-flywheel
metadata:
  version: "1.0.4"
  category: AiAndMachineLearning
description: >-
  ...
---
```

The repository also contains supporting `references/`, `scripts/`, and `assets/` in some skill directories. This is useful for Yuki's future skill catalog: discover and index metadata first, then load only the relevant skill and references on demand.

## Curated shortlist for Yuki

Versions below were read from the upstream `SKILL.md` frontmatter at the reviewed revision.

| Upstream skill ID | Version | Upstream category | Yuki application | Adaptation decision |
|---|---:|---|---|---|
| `agent-platform-eval-flywheel` | 1.0.4 | AiAndMachineLearning | Task-quality datasets, evaluation metrics, failure clustering, iterative improvement | Adopt evaluation methodology; keep evaluator execution local-first and require approval for expensive or remote evaluation runs |
| `google-cloud-solution-multi-agent-security` | 1.0.1 | MultiProductSolutions | Agent trust boundaries, ingress/egress separation, policy enforcement, tool authorization | Extract security principles; do not copy Google Cloud Gateway deployment assumptions into local Yuki |
| `agent-platform-rag-engine-management` | 1.0.2 | AiAndMachineLearning | Grounded retrieval, corpus lifecycle, source-aware answers | Adapt retrieval/data provenance ideas; do not require Vertex AI or a cloud corpus |
| `agent-platform-model-registry` | 1.0.2 | AiAndMachineLearning | Model inventory, immutable versions, provenance, lifecycle state | Build a local model manifest/registry; no cloud registry dependency |
| `agent-platform-inference` | 1.0.1 | AiAndMachineLearning | Provider selection, inference API boundaries, endpoint troubleshooting | Adapt provider abstraction and diagnostics; preserve Ollama/local inference as first-class |
| `agent-platform-skill-registry` | 1.0.1 | AiAndMachineLearning | Skill discovery, metadata, revision history, enable/disable lifecycle | Strong candidate for Yuki's own local skill registry |
| `agent-platform-architecture` | 1.0.0 | AiAndMachineLearning | Identity, governance, multi-agent topology, audit and fail-closed controls | Extract architecture/security patterns only; its target platform is Google Gemini Enterprise |
| `finding-google-skills` | 1.1.0 | MultiProductSolutions | Search and select additional Google technical references | Useful discovery helper, not a runtime skill Yuki needs to execute automatically |

### Highest-value takeaways

1. **Evaluation flywheel:** build repeatable datasets, stable rubrics, failure analysis, and before/after comparisons. Keep Yuki's own task traces and human review as the source of truth. A model grading itself is not independent proof of correctness.
2. **Skill registry:** treat skill metadata and revision history as first-class data. Search metadata before loading full instructions; load supporting references just in time.
3. **Multi-agent security:** every agent and tool needs an explicit identity, allowed capabilities, bounded data access, and auditable policy decisions. Never trust a skill merely because it came from a known repository.
4. **RAG and provenance:** retain source IDs, revision/version, retrieval evidence, and citations alongside knowledge. Retrieved skill text is reference material, not a privileged instruction channel.
5. **Model registry:** track model name, provider, local path or artifact digest, quantization, architecture, license, source, version, hardware requirements, and evaluation results.
6. **Inference abstraction:** standardize request/response, streaming, cancellation, timeout, health, and capability metadata without coupling orchestration to one vendor.

## Proposed Yuki skill metadata contract

For each imported or native skill, store a normalized manifest. Preserve unknown upstream fields rather than silently dropping them.

| Field | Purpose |
|---|---|
| `skill_id` | Stable, unique ID; namespace imported IDs (e.g. `google.agent-platform-eval-flywheel`) |
| `name`, `description` | Human-readable metadata and retrieval routing |
| `version` | Upstream skill version, separate from Yuki's import revision |
| `source.repository` | Canonical repository |
| `source.path` | Exact upstream skill path |
| `source.commit` | Immutable upstream commit SHA |
| `source.content_sha256` | Integrity digest of imported content |
| `license.spdx_id` | License identifier and notice location |
| `category`, `tags` | Discovery taxonomy |
| `requires` | Runtime, packages, tools, network, credentials and platform requirements |
| `capabilities` | Declared read/write/network/process/filesystem capabilities |
| `risk_level` | Static assessment; never inferred only from description |
| `approval_policy` | Whether invocation or individual actions require owner approval |
| `trust_state` | `discovered`, `quarantined`, `reviewed`, `enabled`, `disabled`, `revoked` |
| `entrypoint`, `references`, `assets` | Relative paths, with path traversal protections |
| `imported_at`, `reviewed_at`, `reviewer` | Audit and lifecycle metadata |

## Safe import and execution design

- Discover and parse metadata without executing scripts or installing dependencies.
- Pin imports to a commit SHA and record a content digest. Never trust a moving `main` branch as an immutable version.
- Treat all imported Markdown, examples, scripts, and reference files as untrusted data until reviewed. Prompt injection in skill text must not override Yuki's system policy.
- Quarantine newly discovered skills. Require explicit review before enabling any skill that can access files, network, credentials, shell, or write-capable tools.
- Parse YAML with safe loaders; reject duplicate keys, malformed frontmatter, oversized files, symlinks escaping the skill root, and path traversal.
- Build a capability declaration and enforce it at runtime. Documentation saying a skill is read-only does not itself make its tools read-only.
- Do not run upstream scripts during indexing or metadata extraction. Static-scan them separately and execute only in a sandbox with least privilege.
- Keep optional cloud credentials out of skill packages. A Google Cloud skill may document an integration, but Yuki must remain functional without Google credentials.
- Preserve Apache-2.0 LICENSE and NOTICE material for any copied/modified upstream content. Re-check per-file licensing and bundled assets before importing beyond the repository's top-level license.
- Record import provenance and audit every enable, disable, update, and invocation. Updates should be staged and reviewed, not silently auto-applied.

## Recommended implementation sequence

1. Add a metadata-only skill catalog and parser with strict frontmatter validation.
2. Add a pinned-source importer that stores skill content under the user's Yuki data directory, outside the source repository.
3. Add quarantine/review/enable states and a local skill registry API.
4. Add capability declarations and policy checks before a skill can invoke tools.
5. Add just-in-time loading of `SKILL.md` and referenced documents; never preload the entire catalogue into the model context.
6. Add regression tests for malicious instructions, YAML edge cases, path traversal, unpinned updates, missing license files, and unauthorized tool calls.

## Deliberately not adopted

- Google Cloud project provisioning, billing, IAM roles, Agent Gateway, Vertex/Agent Platform SDKs as mandatory dependencies.
- Instructions that require cloud credentials, remote model calls, or remote compute for ordinary local tasks.
- Automatic execution of imported shell scripts or deployment instructions.

## Sources

- Google Agent Skills repository: https://github.com/google/skills
- Upstream README: https://github.com/google/skills/blob/496fbede89dac1faf0d180a936bfc472b6fbf886/README.md
- Upstream Apache 2.0 license: https://github.com/google/skills/blob/496fbede89dac1faf0d180a936bfc472b6fbf886/LICENSE
- Google Skills agent learning paths: https://www.skills.google/paths?pathslistid=agents
