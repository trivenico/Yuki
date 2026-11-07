# Phase 198 — Soup integration assessment

**Status:** research assessment and implementation decision  
**Reviewed:** 2026-11-07  
**Upstream:** https://github.com/MakazhanAlpamys/Soup  
**Product:** https://trysoup.dev/  
**Upstream license reported by repository:** Apache-2.0

## Executive decision

Adopt Soup's workflow patterns and evaluate selected implementation components; do not wholesale-copy the repository or make Yuki depend on Soup's CLI. Yuki already has substantial pieces of the training governance plane. The opportunity is to connect them into a coherent, provider-neutral model-improvement lifecycle.

The upstream README and site describe a broad post-training workflow: data preparation, method/config selection, training, evaluation gates, registry/lineage, serving/export and supply-chain evidence. Soup's documentation also highlights strict config validation, hardware-fit checks, and a beta layer-streaming technique. These are useful patterns, not proof that every feature is production-ready for Yuki's workload.

## Existing Yuki capabilities discovered

| Area | Existing Yuki implementation | Assessment |
|---|---|---|
| Dataset intake | `providers/dataset_artifact_manifest.py`, `providers/dataset_store.py`, dataset record/archive modules | Provenance, rights, privacy, consent and quarantine foundations already exist |
| Training intent | `providers/training_experiment.py` | Content-addressed experiment specification; LoRA/QLoRA/full-finetune intent; pinned base-model and dataset evidence; split leakage checks; manual approval policies |
| Model evaluation | `providers/model_evaluation.py`, `providers/unified_model_evaluation.py`, evaluation package | Bounded evaluation exists; expected-term coverage is a narrow proxy and must not be treated as general model quality |
| Model registry | `providers/model_registry.py` | Evaluation records and human review exist; richer immutable artifact and parent lineage should be added |
| Training execution | Phase 171/173 architecture docs and compute-worker protocols | Provider-neutral plan exists; do not claim an end-to-end production training worker is available |
| Upgrade governance | Reviewed upgrade proposals, execution journal, evidence verification and leases | This protects software upgrades, not model checkpoint promotion; keep those approval domains separate |

## Adopt, adapt, defer

### Adopt now

1. **Strict, versioned recipes.** Keep one schema as the source of truth; reject unknown fields; normalize values; fingerprint canonical configuration. Bind every recipe to base-model digest, dataset/split digest, benchmark version, seed and environment.
2. **Training preflight.** Before scheduling, report dataset eligibility, model license/rights, parameter count, context length, method, quantization, estimated memory, available worker resources, expected artifact storage and budget. Distinguish hard rejection from uncertain estimates.
3. **Data quality pipeline.** Add content-level format validation, malformed-row reporting, duplicate detection, PII scanning and quality summaries. Never overwrite source datasets; write a new content-addressed revision. Keep untrusted data quarantined until review.
4. **Candidate-versus-baseline evaluation.** Evaluate task performance, general capability retention, safety, refusal behavior, latency, throughput and cost. Persist a machine-readable verdict with exact candidate/base/data/benchmark digests.
5. **Model lineage.** Store immutable model artifact records with parent model, adapter/base digest, experiment ID, training recipe, dataset and split digests, evaluation evidence, license/rights evidence and artifact checksum.
6. **Honest CLI and desktop status.** Surface preflight, queued/running/failed/completed jobs, logs, resource usage, evaluation verdict and review state. Never display a planned or simulated job as running.

### Adapt after correctness review

- Soup's ready-made recipes: import concepts and selected parameter defaults only after validating supported model architecture, tokenizer, license and Yuki's backend compatibility.
- TRL/Transformers training: implement behind a provider-neutral backend contract, with pinned dependency versions and deterministic small fixtures in CI.
- MLX/Unsloth: optional backends selected by detected worker capability; do not make either a core dependency.
- Adapter diff/merge/bisect: add after artifact lineage and reproducible evaluation are reliable.
- Data mixing and synthetic-data generation: require source attribution, privacy checks, contamination checks and held-out evaluation.

### Defer

- **Layer streaming:** optional experimental backend only. Upstream explicitly labels it beta and documents correctness repairs and limitations. Require bit-exact forward checks where applicable, gradient equivalence, trained-quality comparison, OOM boundaries and hardware-specific benchmarks before enabling it.
- Full fine-tuning and large-scale RL: require explicitly approved GPU capacity, budget and rollback plans.
- Automatic model activation: remain prohibited without independent evaluation, rights review and human release approval.
- Copying Soup wholesale: unnecessary duplication, larger dependency/security surface, and tight coupling to upstream CLI/config internals.

## License and supply-chain handling

The Soup repository reports Apache-2.0. Before importing any source file or dependency:
- capture the exact upstream commit SHA and repository LICENSE/NOTICE;
- retain required copyright, license and NOTICE text;
- mark every modified file;
- review transitive dependencies, Python/runtime compatibility and security advisories;
- record source URL, commit, file paths, local modifications and checksum in Yuki's third-party inventory;
- keep upstream names and trademarks distinct from Yuki branding.

This assessment does not copy Soup source code. Reuse should be selective and attributable.

## Proposed Yuki model-improvement lifecycle

1. Curate a new immutable dataset revision; validate schema, rights, privacy, consent and quality.
2. Create a strict recipe and content-addressed experiment spec.
3. Run a preflight against a paired worker's current capabilities and an explicit cost/resource ceiling.
4. Obtain explicit training approval.
5. Execute on an approved local or remote worker with pinned source, environment, base-model and dataset digests.
6. Persist checkpoints incrementally to durable artifact storage; never rely on ephemeral worker disks.
7. Verify the result manifest and artifact bytes, then register the candidate in quarantine.
8. Run candidate-versus-baseline task, regression, safety and operational evaluations.
9. Produce a signed, digest-bound SHIP / DON'T-SHIP report.
10. Require human model-release approval before staging or activation; preserve the previous checkpoint for rollback.

## Implementation sequence

| Milestone | Deliverable | Gate |
|---|---|---|
| 1 | Training preflight report and recipe schema | Unknown fields rejected; estimates labelled as estimates |
| 2 | Dataset content validator and immutable output revisions | Input never overwritten; PII and rights checks fail closed |
| 3 | Model artifact lineage registry | Parent and all source digests verifiable |
| 4 | Provider-neutral training worker contract | Pinned inputs, bounded resources, cancellation and durable artifacts |
| 5 | Transformers/TRL backend | Reproducible small run and worker-level CI |
| 6 | Candidate evaluation and release gate | No promotion on regression or missing evidence |
| 7 | CLI and desktop integration | Shared API and truthful lifecycle state |
| 8 | Optional MLX/Unsloth/layer-streaming backends | Backend-specific correctness and benchmark evidence |

## Explicit non-goals and current constraints

- No foundation model training from random initialization.
- No uploading private conversations or personal memory into external training datasets by default.
- No automatic training or model activation merely because a provider job succeeded.
- No GPU training is claimed from GitHub Actions CPU test results.
- The current project owner has no PC/laptop available; continue source, schema, security and CI work remotely. Defer local GPU benchmarking and hardware-specific validation until an actual worker is available.

## Upstream references reviewed

- Soup repository and README: https://github.com/MakazhanAlpamys/Soup
- Soup product and workflow overview: https://trysoup.dev/
- Soup documentation index: https://github.com/MakazhanAlpamys/Soup/blob/main/docs/README.md
- Model registry: https://trysoup.dev/docs/registry
- Data pipeline: https://github.com/MakazhanAlpamys/Soup/blob/main/docs/data.md
- Contributor architecture map: https://trysoup.dev/docs/contributing
