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

The importer rejects unknown fields, duplicate JSON keys and IDs, malformed geometry, non-finite/out-of-range coordinates, invalid text offsets, malformed optional asset SHA-256 fingerprints, oversized lines, excessive record counts, and oversized input. It records a SHA-256 digest and source label as owner-supplied provenance. Optional `asset_sha256` values are normalized to lowercase and allow downstream exact-duplicate grouping; the importer does not read asset files or verify that the supplied digest matches their bytes.

## Dataset manifests and split integrity

The `knowledge.dataset_manifest` module creates deterministic schema-v1 manifests from an imported annotation dataset. It records the source dataset digest, sorted asset-to-split assignments, leakage-group identifiers, per-split counts, and a digest over the manifest content. Every asset must be assigned exactly once to `train`, `validation`, or `test`; assets sharing an explicitly supplied leakage group must remain in the same split. Empty datasets and unknown assets/groups are rejected.

Manifests preserve `owner_supplied_unverified` provenance. Hashes provide integrity/change detection, not source authenticity, annotation correctness, reviewer identity, or proof that a split is statistically representative. Explicit grouping must still be supplied by the dataset owner when assets share a subject, capture session, source document, near-duplicate family, or other leakage risk. When multiple asset IDs carry the same supplied SHA-256 fingerprint, the manifest automatically enforces a shared split. It cannot discover visual near-duplicates, and it does not independently verify supplied fingerprints against asset bytes.

## Human review, adjudication, and agreement

The `knowledge.annotation_review` module adds immutable review records bound to the canonical digest of the exact annotation reviewed. Each review stores a bounded reviewer label, decision (`accept`, `reject`, or `needs_context`), confidence, rationale, and timezone-aware timestamp. Reviewer labels are display labels, not authenticated identities; review provenance remains unverified.

Adjudication is a separate immutable record that binds an explicit human decision and rationale to the annotation digest and the exact set of source review digests. It never overwrites or deletes reviewer opinions. Agreement metrics report the number of multiply reviewed annotations, unanimous decisions, disagreements, exact agreement ratio, and unresolved disagreements. Only one review per reviewer label is counted for an annotation. The ratio is unweighted exact decision agreement, not Cohen's/Fleiss' kappa or a claim of statistical reliability. An adjudication marks a disagreement as resolved for workflow reporting but does not erase the disagreement or make the resulting label independently verified.

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
2. Schema-versioned dataset manifests, explicit asset splits, owner-supplied leakage groups, and exact-duplicate grouping from supplied SHA-256 fingerprints (implemented); fingerprint verification and near-duplicate discovery remain outstanding.
3. Read-only adapters for CVAT, Label Studio, doccano, VIA, LabelMe, and Argilla exports.
4. Immutable annotation reviews, digest-bound human adjudication, and basic exact-agreement metrics (implemented); authenticated reviewer identity, rubric versioning, inter-rater statistics, and preference/RLHF record formats remain outstanding.
5. Optional connectors for commercial platforms only after per-service consent, secure secret handling, retention review, and explicit network permissions.
6. Evaluation harness that compares models against held-out, reviewed datasets; training/fine-tuning stays a separate approved workflow.

## Current limitations

The initial importer is not yet a direct parser for any listed vendor export. It supports a normalized Yuki JSONL interchange format only. It does not render annotation interfaces, upload datasets, invoke vendors, train models, or claim annotation quality.
