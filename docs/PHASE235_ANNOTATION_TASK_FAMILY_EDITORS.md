# Phase 235 — Task-family annotation editor contracts

## Delivered

- Adds UI-neutral field descriptors for all supported annotation task families.
- Classification labels are exposed as rubric-derived choices when a label list exists.
- Scalar confidence values are parsed as finite numbers; repeated/nested records use
  bounded JSON fields with family-specific help and expected container types.
- Payload construction delegates final schema, bounds, rubric, timestamp, offset,
  rank and score validation to `core.annotation_validation`; it does not create a
  second validation authority.
- Custom tasks retain the existing JSON-object contract.
- The builder is side-effect free: it neither persists drafts nor submits work.

## Safety and limitations

- Form metadata is suitable for Textual and desktop clients; it is not itself a
  graphical annotation canvas.
- Bounding boxes, polygons, tracks and segment lists are currently entered as
  structured JSON arrays, not manipulated directly over image/audio/video previews.
- No asset is opened or downloaded by this layer.
- The caller must retain explicit human review/confirmation and use the existing
  owner-scoped store submission method with the task's expected version.
- No model assistance, marketplace automation, client delivery or payment action
  is performed.
