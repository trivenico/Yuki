# Phase 173 — GitHub + Hugging Face training infrastructure

**Status:** architecture decision  
**Decision:** use GitHub and Hugging Face as complementary, replaceable providers. Neither is the source of truth for Yuki's model governance.

## 1. Responsibilities

| Capability | GitHub | Hugging Face | Yuki-owned control plane |
|---|---|---|---|
| Source code and reviews | Primary | Optional mirrored scripts | Pins exact repository commit |
| CI and code quality | Primary Actions workflows | Jobs can run pinned scripts | Verifies results |
| Model discovery | Links/references | Primary discovery surface | Candidate registry and license review |
| Dataset discovery | Links/references | Primary discovery surface | Provenance, privacy, rights and quality gates |
| Training compute | Actions for CI/light tasks; self-hosted runners where appropriate | Jobs GPU/CPU/TPU compute | Chooses provider and enforces budget/approval |
| Model artifacts | Release metadata; avoid large weights in Git | Model repositories and Storage Buckets | Digest verification, lineage and release catalog |
| Demonstrations | GitHub Pages/releases as suitable | Spaces | Product deployment policy |

Hugging Face Hub repositories are versioned and can host model, dataset and app assets. Hugging Face Jobs can run user scripts or container images on selected hardware and upload outputs to the Hub. Jobs are pay-as-you-go and require a positive credit balance. Storage Buckets can preserve checkpoints across job/container lifetimes. See the official references below.

## 2. Recommended Yuki workflow

1. GitHub PR merges reviewed training code and immutable experiment definitions.
2. Yuki's control plane verifies the base-model revision/digest, dataset manifests, rights evidence, split digest, benchmark baseline, budget and explicit training approval.
3. A provider adapter submits a pinned training job to one of:
   - local paired worker (Ollama for inference; PyTorch/Transformers/TRL for training),
   - Hugging Face Jobs,
   - approved self-hosted GPU worker,
   - later, another compatible GPU provider.
4. The job reads only approved, version-pinned inputs and receives narrowly scoped secrets at runtime. No user conversation or private memory is uploaded unless separately consented and approved.
5. Checkpoints and logs are written to durable storage during long jobs; do not rely on ephemeral job disks.
6. The worker returns a result manifest with experiment ID/digest, source commit, base-model digest, dataset/split digests, training configuration, checkpoint digest, metrics, runtime/hardware and provider job ID.
7. Yuki verifies downloaded artifact bytes and manifest consistency, then registers the checkpoint in quarantine.
8. Independent evaluation, rights/provenance review and human release approval are required before a Yuki model can be staged or activated.

## 3. Hugging Face-specific capabilities and constraints

- Hub: discover and version model/dataset repositories; pin exact revisions and verify downloaded artifact hashes.
- Jobs: run custom scripts or Docker images on selected hardware; jobs can continue after the submitting terminal closes.
- Hardware: choose supported CPU/GPU/TPU flavors based on measured memory/compute needs. Availability, limits and prices can change; query them at launch time.
- Persistence: job disks are ephemeral. Push completed artifacts to a private model repo and/or write resumable checkpoints to a Storage Bucket during execution.
- Secrets: pass only short-lived, least-privilege tokens to the job. Never commit tokens to GitHub, training configs, logs or model artifacts.
- Dataset streaming: use streaming/mounting for datasets larger than local job disk; preserve source revision and record exact content provenance.
- AutoTrain: do not make it a core dependency; the official AutoTrain documentation currently says the project is no longer maintained and recommends alternatives such as TRL, Axolotl or Transformers Trainer.

## 4. Non-negotiable safeguards

- Hugging Face account/token is optional. Yuki must retain local and alternate-provider paths.
- A Hub listing is not proof that a model/dataset license permits training, commercial use or redistribution.
- Never execute unreviewed remote repository code or model custom code by default.
- Pin training scripts and container images to immutable commits/digests, not moving `main` or `latest` tags.
- Enforce per-experiment spend ceilings, wall-clock timeouts, cancellation and concurrency quotas.
- Keep private datasets private; apply consent, privacy and retention checks before any remote transfer.
- Treat provider logs and job status as operational evidence, not proof of a correct or safe model.
- A successful training process does not automatically make its checkpoint a Yuki release.
- Maintain exportable manifests and artifacts so Yuki can move providers without losing lineage.

## 5. Implementation sequence

1. Provider-neutral immutable training-job manifest and result-manifest schema.
2. Local worker adapter with deterministic dry-run and bounded execution.
3. Hugging Face Jobs adapter with explicit account opt-in, budget checks and secret forwarding.
4. Artifact pull/verification and checkpoint resume support.
5. Evaluation and release-gate integration.
6. Additional cloud providers behind the same adapter contract.

No training job is launched by this architecture document. The current training experiment builder records approved intent but does not download weights or execute training.

## 6. Official references

- Hugging Face Hub: https://huggingface.co/docs/hub/main/index
- Hugging Face Jobs training: https://huggingface.co/docs/hub/jobs-training
- Jobs execution and lifecycle: https://huggingface.co/docs/huggingface_hub/en/guides/jobs
- Large dataset processing: https://huggingface.co/docs/hub/jobs-large-datasets
- AutoTrain maintenance notice: https://huggingface.co/docs/autotrain/main/index
