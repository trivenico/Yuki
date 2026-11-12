# Phase 241 — Authenticated annotation canvas workbench

## Delivered

- Adds an annotation workbench to Yuki's existing same-origin HTML/CSS/JavaScript web client; no new frontend framework or runtime dependency.
- Uses the existing authenticated session and owner-scoped annotation project/task APIs.
- Supports creating human-only projects and importing image assets for visual task families.
- Image upload uses the existing binary asset endpoint. Tasks are created only after the configured scanner returns a clean result; quarantined uploads remain unavailable.
- Loads image content only through the authenticated safe-preview endpoint. The browser receives a metadata-stripped PNG derivative as a Blob URL; the CSP explicitly allows Blob-backed image rendering.
- Provides image fit, cursor-anchored wheel zoom, bounded pan, keyboard pan/zoom/reset, pointer-captured bounding-box gestures, and polygon vertex editing.
- Persists normalized source-image coordinates in the submission payload, not viewport pixels.
- Supports image classification, bounding boxes, and polygon segmentation visually. Other supported task families can use the structured JSON payload editor.
- Uses the existing optimistic task version and explicit confirmation prompt; submissions are marked `ai_assisted=false` and `human_confirmed=true`.
- Adds static-asset allowlisting and tests for the web shell, CSP, and annotation asset delivery.

## Security and operational boundaries

- Authentication and owner/project/asset scope are enforced by the existing API. The browser never receives the raw stored asset.
- Asset upload requires an explicitly configured scanner to mark the file clean. Without one, uploads remain quarantined and no task is created.
- Server-side task-family validation and version checks remain authoritative; browser validation is only immediate feedback.
- No marketplace scraping, external submission, payment action, automatic task claiming, or background work is implemented.
- No third-party scripts, inline scripts, or new runtime packages are introduced.

## Current limitations

- This is the first usable visual workbench, not a complete professional annotation suite.
- Unsaved geometry is in-memory and is lost on refresh; no draft autosave or undo/redo UI is included.
- Existing annotations cannot yet be individually selected, moved, resized, relabeled, or deleted.
- The canvas handles still images. Video timelines, audio waveforms, diarization controls, and review/acceptance screens are future work.
- The browser UI depends on the server being constructed with both `annotation_workbench` and `annotation_assets`, plus a real scanner callback. This PR does not silently configure a scanner.
- Preview decoding still occurs in the API process; this does not establish process-level decoder isolation.

## Validation

- Repository CI is the source of truth for this branch's tests.
- No local test run is claimed.
