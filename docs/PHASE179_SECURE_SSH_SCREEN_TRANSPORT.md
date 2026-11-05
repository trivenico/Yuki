# Phase 179 — Secure SSH screen transport foundation

## Delivered

- Build a fixed SSH command for the pre-installed
  `/usr/local/libexec/yuki-screen-capture` helper.
- Require strict host-key checking and an explicit known_hosts file.
- Use batch mode, identity-only authentication, bounded connect timeout and
  no TTY allocation.
- Validate host, username, session identifier, port and absolute key paths.
- Do not accept caller-supplied remote shell commands.
- Validate returned PNG signature, IHDR dimensions and a pixel-count ceiling.
- Require the explicit screen-observation grant before accepting a frame.

## Not implemented

This module builds an argv and validates a returned frame; it does not invoke
SSH, install a helper, capture a screen, OCR pixels, or control a desktop.
A platform-specific remote helper must be separately installed and reviewed.
Its service account should be unprivileged, restricted to the active graphical
session, and unable to read arbitrary files or invoke a shell command.

## Operational requirements

- Provision the host key out-of-band; never use `StrictHostKeyChecking=no`.
- Use a dedicated SSH key restricted server-side to the screenshot helper
  (forced command, no PTY, no forwarding, no agent forwarding, no port
  forwarding, no X11 forwarding).
- Keep capture permission distinct from remote input and file-transfer grants.
- Redact sensitive UI regions before cloud vision processing; prefer local OCR.
- Do not persist screenshots or include their contents in logs by default.
