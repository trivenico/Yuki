# Phase 65 — Scheduled Hugging Face model scout

Yuki can run a bounded read-only snapshot of public Hugging Face model metadata across six allow-listed task families and upload the report as a GitHub Actions artifact.

## Automatic and manual runs

- Schedule: every Monday at 09:17 UTC.
- Manual run: Actions → **Yuki Hugging Face model scout** → Run workflow.
- The workflow uses public metadata; it does not need `HF_TOKEN`.
- Reports are uploaded as an artifact with 30-day retention. They are not committed to the repository.
- The workflow requests only repository read permission.

The workflow is not a push notification service and does not guarantee detection of every newly released model. It takes a bounded snapshot sorted by upstream `lastModified`, deduplicates model IDs, and lists recent metadata. A later phase can diff snapshots to detect new IDs and metadata changes.

## Run locally

```bash
python yuki_hf_model_scout.py \
  --limit-per-task 10 \
  --output-dir artifacts/model-scout
```

The limit is 1–50 models per task across text generation, text-to-text, conversational, feature extraction, image-text-to-text, and automatic speech recognition. Each report is timestamped; existing reports are not overwritten.

## Safe adoption workflow

1. Download the report artifact from a successful scout run.
2. Review each model's official model card, license terms, provenance, supported hardware/runtime and security posture.
3. Evaluate shortlisted models with the Phase 63 benchmark runner.
4. Record results in the Phase 64 registry using a stable benchmark version.
5. Only mark license/runtime status as verified after the corresponding checks have actually been completed.
6. Make any activation decision separately, with tests, rollback and human approval.

The scout never downloads model weights, executes model-provided code, installs packages or changes the active model. Model metadata such as likes, downloads and modification timestamps is not proof of quality, safety, license compatibility or runtime support.

## Operations and limitations

- Scheduled workflows run only while GitHub Actions is enabled and repository scheduling is permitted.
- GitHub can delay scheduled workflows; use workflow_dispatch for an on-demand run.
- Artifact retention is 30 days; download reports you need to keep longer.
- This phase creates periodic snapshots, not a durable database or model-change alerting system.
- Inference/evaluation is deliberately separate because it may send prompts to a third-party provider and consume quota or incur cost.
