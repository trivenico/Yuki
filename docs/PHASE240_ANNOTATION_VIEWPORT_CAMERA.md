# Phase 240 — Annotation viewport camera

## Delivered

- Adds an immutable, framework-independent viewport camera for annotation image presentation.
- Fits images without distortion and preserves the existing normalized-image coordinate model.
- Supports cursor-anchored zoom with bounded zoom levels and stable source-point anchoring.
- Supports pan in viewport pixels while enforcing a configurable visible-image margin so the image cannot be lost entirely.
- Provides normalized-image/viewport coordinate conversion and reset-to-fit.
- Rejects invalid dimensions, non-finite coordinates, boolean numeric values, invalid zoom factors, and invalid camera limits.
- Adds focused tests for fit, zoom anchoring, zoom limits, pan bounds, invalid inputs, round trips, and reset behavior.

## Integration boundary

This is a presentation-state primitive, not a desktop canvas or renderer. A native/web client still owns image decoding/rendering, high-DPI coordinate conversion, pointer capture, keyboard and accessibility behavior, lifecycle cleanup, and secure preview retrieval. Persist only normalized annotation geometry; never persist camera offsets or viewport pixels. The server remains authoritative for asset authorization, task versions, and human-confirmed submission.

No UI framework or new runtime dependency is introduced.
