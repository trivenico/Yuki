# Phase 235 — Task-family annotation editors

## Delivered

- Adds UI-neutral field descriptors for every supported annotation task family.
- Adds a Textual `/edit TASK_ID VERSION` form opened only for an active project and
  a currently claimed task at the exact supplied version.
- Classification forms expose rubric labels as visible choices; scalar confidence
  is parsed as a finite number.
- Bounding boxes, polygons, video tracks, audio/diarization segments, entities,
  rankings and evaluation scores each receive a task-specific structured field
  and concise schema guidance.
- Nested/repeating records are entered as bounded JSON arrays/objects. The form
  builder delegates final schema, coordinate, timing, rubric, offset, rank and
  score validation to `core.annotation_validation`, avoiding a second validation
  authority.
- The UI presents a separate payload review step. Only the explicit
  **Confirm and submit** action calls the owner-scoped store with the task's
  expected version, `human_confirmed=True` and `ai_assisted=False`.
- Edit and cancel remain available before confirmation; stale versions are
  rejected by the backend. Custom tasks retain their JSON-object contract.

## Safety and limitations

- The editor does not auto-claim, auto-save, call a model, fetch assets, or deliver
  work to a client or marketplace.
- Bounding boxes and polygons are still entered as normalized coordinate JSON;
  this is not yet a drag-and-drop image canvas. Audio/video timelines and waveform
  previews are not implemented.
- Asset preview/download, import profiles, marketplace integration, and payments
  remain separate work.
- No external action is performed and no income is guaranteed.
