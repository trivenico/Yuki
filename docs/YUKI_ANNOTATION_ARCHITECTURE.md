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

## Vendor export adapters

The `knowledge.annotation_adapters` module currently provides bounded, read-only import adapters for:
- Label Studio JSON task exports: classification choices, normalized rectangle boxes, polygons, and text spans.
- doccano JSONL sequence-label exports: text spans with source-text offsets.

Both adapters normalize into the canonical annotation model and attach source-format/source-label provenance. Unsupported Label Studio result types are counted in `skipped_records`; malformed supported results reject the import. Neither adapter executes vendor code, contacts a service, uploads data, or authenticates annotators. Asset references are represented by export task/record identifiers rather than fetching external URLs. Direct adapters for CVAT, VIA, LabelMe, Argilla, and commercial platforms remain outstanding.

## Vendor export adapters

The `knowledge.annotation_adapters` module provides bounded, read-only import adapters for:
- Label Studio JSON task exports: classification choices, normalized rectangle boxes, polygons, and text spans.
- doccano JSONL sequence-label exports: text spans with source-text offsets.
- CVAT image-task XML: rectangle boxes and polygons. Video tracks, masks, skeletons, and other unsupported shape types are skipped and counted.
- VIA v2 project JSON: rectangle boxes and polygons.

All supported imports normalize through the canonical annotation JSONL contract and retain source-format/source-label evidence with unverified provenance. Unsupported shapes are counted; malformed supported shapes reject the import. CVAT DTD/entity declarations are rejected before XML parsing. The adapters do not fetch image paths, execute vendor code, contact vendor services, upload data, or authenticate annotators. CVAT video interpolation, masks, skeletons, VIA polyline/point/ellipse/circle, and direct service APIs remain unsupported.

## Human review, adjudication, and agreement

The `knowledge.annotation_review` module adds immutable review records bound to the canonical digest of the exact annotation reviewed. Each review stores a bounded reviewer label, decision (`accept`, `reject`, or `needs_context`), confidence, rationale, and timezone-aware timestamp. Reviewer labels are display labels, not authenticated identities; review provenance remains unverified.

Adjudication is a separate immutable record that binds an explicit human decision and rationale to the annotation digest and the exact set of source review digests. It never overwrites or deletes reviewer opinions. Agreement metrics report the number of multiply reviewed annotations, unanimous decisions, disagreements, exact agreement ratio, and unresolved disagreements. Only one review per reviewer label is counted for an annotation. The ratio is unweighted exact decision agreement, not Cohen's/Fleiss' kappa or a claim of statistical reliability. An adjudication marks a disagreement as resolved for workflow reporting but does not erase the disagreement or make the resulting label independently verified.

## Rubric versioning and pairwise preference feedback

`knowledge.annotation_rubric` defines immutable rubric versions with bounded criteria, integer weights, timezone-aware creation timestamps, and canonical SHA-256 integrity digests. A changed rubric is a new version and produces a different digest; records never silently inherit edits to an earlier version.

`knowledge.preference_feedback` stores pairwise comparisons over a prompt and two distinct candidate responses. It retains only SHA-256 digests of prompt/response text, the exact rubric digest, preference (`a`, `b`, `tie`, or `neither`), reviewer display label, rationale, timestamp, and a canonical record digest. Validation rejects tampering and rubric-version mismatch. Reviewer labels are not authenticated identities; provenance remains unverified. These records are evaluation inputs only: they do not trigger fine-tuning, reward-model training, model selection, or deployment.

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
3. Read-only Label Studio, doccano, CVAT image-task XML, and VIA v2 JSON adapters (implemented); LabelMe and Argilla adapters remain outstanding.
4. Immutable annotation reviews, digest-bound human adjudication, and basic exact-agreement metrics (implemented); authenticated reviewer identity, advanced inter-rater statistics, and durable review storage remain outstanding; rubric versioning and digest-bound pairwise preference records are implemented.
5. Optional connectors for commercial platforms only after per-service consent, secure secret handling, retention review, and explicit network permissions.
6. Evaluation harness that compares models against held-out, reviewed datasets; training/fine-tuning stays a separate approved workflow.

## Current limitations

The canonical importer uses normalized Yuki JSONL internally; the read-only vendor adapters translate supported export profiles into that contract. The adapters do not render annotation interfaces, upload datasets, invoke vendors, train models, or claim annotation quality.
