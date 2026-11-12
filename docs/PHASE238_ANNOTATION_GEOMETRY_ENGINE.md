# Phase 238 — Annotation geometry engine

## Scope

This phase adds the framework-independent geometry and history layer needed by
future visual annotation clients. It does not claim to deliver an interactive
canvas or a desktop rendering surface.

## Contracts

- Persisted points and boxes use normalized image coordinates in [0, 1].
- Viewport fitting preserves image aspect ratio and computes letterbox offsets.
  Pointer coordinates in letterbox regions map to no image point.
- Bounding-box creation supports reverse-direction drags, clamps through
  normalized input validation, and rejects sub-pixel gestures.
- Polygon validation enforces bounded vertex count, finite normalized points,
  adjacent-vertex uniqueness, non-zero area, and rejects non-adjacent edge
  intersections and adjacent collinear overlaps.
- SnapshotHistory provides defensive copies, bounded undo history, redo, and
  redo invalidation after a new edit.

## Integration and safety

- This module has no UI framework, network, filesystem, or persistence access.
- The canonical server-side annotation schema remains
  core.annotation_validation.validate_annotation_payload.
- Viewport pixels must never be persisted; only normalized geometry should be
  submitted, bound to the current task version.
- Geometry editing must remain separate from explicit human review/confirmation.
- Next phase: integrate the primitives into a real desktop image canvas and add
  pointer capture, keyboard accessibility, and end-to-end task submission tests.
