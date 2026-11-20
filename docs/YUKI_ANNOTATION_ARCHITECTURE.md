# Yuki Human Annotation and Feedback Architecture

## Goal

Provide one provenance-aware dataset and feedback layer for visual, document, text, audio, video, and model-response annotation. Integrations are adapters around a canonical internal contract, not a collection of bundled vendor runtimes.

## Ecosystem mapping

| Ecosystem | Intended adapter direction | Initial boundary |
|---|---|---|
| CVAT | Image/video boxes, polygons, tracks, keypoints | Import/export only; no remote project mutation |
| Label Studio | Multi-modal tasks and predictions | Normalize reviewed task exports |
| Prodigy | Text/image annotation recipes | Import reviewed annotations; recipe execution remains separate |
| doccano | Text classification and sequence labels | Classification/span interchange |
| VGG Image Annotator (VIA) | Image regions and attributes | Region geometry conversion |
| LabelMe | Image polygons and shapes | Polygon conversion |
| Argilla | Dataset records, responses, feedback | Human-feedback interchange |
| Labelbox, SuperAnnotate, Encord, V7 | Commercial image/video/data workflows | Explicit user-export adapters; no credentials or API access by default |
| Scale AI, Appen, TELUS International and RLHF vendors | Human labeling and preference data | Contracted, consented, de-identified export/import; vendor-neutral task and review records |

## Canonical data contract (initial)

The knowledge.annotation module currently supports bounded JSONL records for:
- Classification labels.
- Normalized bounding boxes.
- Normalized polygons.
- Text spans with source-text offsets.

The importer rejects unknown fields, duplicate JSON keys and IDs, malformed geometry, non-finite/out-of-range coordinates, invalid text offsets, oversized lines, excessive record counts, and oversized input. It records a SHA-256 digest and source label as owner-supplied provenance.

## Trust and privacy rules

- Imported annotations are unverified; a vendor export or annotator label is not proof of correctness.
- Keep source dataset IDs, task IDs, annotation IDs, timestamps, annotator pseudonyms, schema versions, and provenance when adapter support is added.
- Do not upload private files or datasets to third-party services without explicit owner choice and disclosure of destination, retention, and processing terms.
- Minimize or redact personal data; do not infer sensitive traits or use annotation workflows for covert surveillance.
- Separate annotator identity from exported training data; never place credentials or access tokens in datasets.
- Preserve disagreement, abstention, and uncertainty. Do not collapse multiple human judgments into a single truth label without a documented adjudication policy.
- RLHF preference pairs and critique records require explicit rubric/version, source prompt provenance, reviewer decision, and adjudication metadata. They are evaluation/training inputs, not automatic permission to fine-tune or deploy a model.
- Dataset imports and exports are inert data operations. They do not run vendor scripts, plugins, model code, or arbitrary transformations.

## Delivery sequence

1. Canonical schema and bounded normalized JSONL importer (implemented).
2. Schema versioning, task/asset manifests, dataset splitting and leakage checks.
3. Read-only adapters for CVAT, Label Studio, doccano, VIA, LabelMe, and Argilla exports.
4. Preference/RLHF record format, disagreement and adjudication workflow, quality metrics.
5. Optional connectors for commercial platforms only after per-service consent, secure secret handling, retention review, and explicit network permissions.
6. Evaluation harness that compares models against held-out, reviewed datasets; training/fine-tuning stays a separate approved workflow.

## Current limitations

The initial importer is not yet a direct parser for any listed vendor export. It supports a normalized Yuki JSONL interchange format only. It does not render annotation interfaces, upload datasets, invoke vendors, train models, or claim annotation quality.
