# Phase 239 — Annotation pointer interaction controller

## Delivered

- Adds a framework-independent controller for bounding-box pointer gestures and polygon vertex editing.
- Enforces one active pointer gesture and exact pointer identity for move/release.
- Validates normalized coordinates, releases pointer state on failed commits, and prevents empty labels.
- Commits boxes and polygons only after geometry validation; polygon drafts remain uncommitted until explicitly finished.
- Uses bounded defensive-copy history for undo/redo and returns document snapshots rather than mutable internal state.
- Adds focused unit tests for gesture lifecycle, pointer mismatch, failed gesture cleanup, polygon validation, tool switching, and history isolation.

## Client integration contract

- A desktop/web renderer must convert viewport coordinates with `ViewportTransform.viewport_to_image` before calling this controller.
- The renderer remains responsible for native pointer capture/release, focus, keyboard shortcuts, zoom/pan presentation, and accessible tool controls.
- This controller performs no image loading, filesystem access, network calls, persistence, task submission, or marketplace delivery.
- The server's task-family validator and optimistic task version remain authoritative. A client must submit only through the existing explicit human-confirmation flow.
