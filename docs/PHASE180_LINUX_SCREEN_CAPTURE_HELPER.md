# Phase 180 — Linux desktop screenshot helper

## Implemented

`tools/yuki_screen_capture.py` provides a narrowly scoped Linux screen
capture helper for a logged-in desktop user. It supports:
- Wayland via `grim`.
- X11 via ImageMagick `import`.
- A private, user-owned, mode-0600 local grant file under
  `$XDG_RUNTIME_DIR/yuki-screen-grants/<session>.json`.
- Session-bound grants with a maximum one-hour expiry.
- No root execution, no shell invocation, no arbitrary executable or path
  supplied by the caller, bounded execution time, PNG signature/dimension
  validation and an 8 MiB output cap.

The helper writes PNG bytes to stdout and diagnostics to stderr, making it
compatible with a forced-command SSH key and the existing SSH transport
request builder.

## Grant file

The logged-in user must explicitly create the grant file with:
`{"enabled":true,"session_id":"<session-id>","expires_at_epoch":<unix-time>}`
The grant directory must be mode 0700 and the file mode 0600, owned by the
desktop user. The helper refuses root execution and never creates consent on
the user's behalf.

## Deployment hardening still required

Install the helper root-owned and not writable by the desktop account. Create
a dedicated SSH key with server-side forced command, no PTY, no forwarding,
and no shell access. Verify host keys out-of-band. The SSH service account
must be the desktop user or a carefully configured session-aware helper;
headless SSH sessions without an active graphical environment are rejected.

## Limitations

This captures the whole desktop frame, so sensitive UI may be present. The
caller must apply redaction and honor the grant's sensitive-content policy
before passing pixels to OCR or vision. This helper does not perform OCR,
vision analysis, remote input, or screen recording.
