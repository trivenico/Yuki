# Phase 237 — Safe inline image previews

## Scope

This phase adds an authenticated preview derivative for annotation assets. It
does not add a browser canvas, drawing controls, or an annotation editor UI.

## Security behavior

- Preview requests use the existing bearer-authenticated API and require the
  exact owner, project, and opaque asset ID. The asset store returns bytes only
  for a clean asset; quarantined, blocked, deleted, missing, and foreign assets
  are not previewable.
- Source bytes are never served inline. Pillow decodes a single raster frame
  from PNG, JPEG, WebP, or GIF, enforces dimension and pixel-count limits, and
  re-encodes pixels into a fresh PNG. Metadata, embedded profiles, and trailing
  polyglot bytes are not copied into the derivative.
- Animated images, SVG, PDF, HTML, unsupported formats, malformed images, and
  oversized dimensions are rejected.
- Responses use `image/png`, `nosniff`, `no-store`, a restrictive CSP sandbox,
  same-origin resource policy, and inline disposition for the generated PNG.
- The existing download endpoint remains attachment-only and unchanged. A clean-state metadata/file digest mismatch fails closed with HTTP 409; missing or non-clean assets remain indistinguishable as 404.
- Preview generation runs off the ASGI event loop and is bounded by input
  storage limits, 40 million pixels, 12,000 pixels per dimension, and a 20 MiB
  encoded derivative limit.

## Operational requirements and limitations

- Pillow is a runtime dependency. Deployments must keep it patched and should
  process untrusted image decoding in a resource-limited worker/container for
  stronger isolation; the current API uses a worker thread, not a separate
  process sandbox.
- A clean scanner result remains a prerequisite. This feature does not provide
  or replace malware scanning, and the scanner's effectiveness is deployment-
  specific.
- This endpoint is a safe raster preview primitive, not a full visual
  annotation surface. Coordinate editing, zoom/pan, polygons, boxes, and
  task-family interaction remain future UI work.
- The source asset is re-read and integrity-checked for each preview. Tests cover
  successful re-encoding, owner isolation, quarantined assets, malformed raster
  content, and post-upload file tampering. No derivative cache is persisted,
  avoiding stale derivatives after deletion.
