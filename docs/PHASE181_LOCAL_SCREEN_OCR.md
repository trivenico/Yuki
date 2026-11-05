# Phase 181 — Local screen OCR adapter

## Delivered

`providers/ocr_adapter.py` adds a local Tesseract adapter for PNG frames.
It first validates the active screen-observation grant and image frame, then
runs the locally installed Tesseract executable with image bytes on stdin.
The command is passed as an argv list with `shell=False`.

Controls:
- Local-only execution; no network or remote API call.
- Supported language identifier restricted to a conservative character set.
- Maximum 20-second timeout (12 seconds by default).
- Maximum 8 MiB image payload inherited from the screen grant.
- Maximum 64 KiB extracted text output.
- No persistence or logging of pixels or extracted text.
- Clear failure when Tesseract is unavailable.

## Install

Linux distributions commonly package Tesseract as `tesseract-ocr`; language
data packages must be installed separately (for example, English data).
Yuki does not install system packages automatically.

## Limitations / next hardening

This adapter uses Tesseract's stdout pipe and checks the returned text size
after the subprocess exits. For hostile or unusually complex images, a future
hardened runner should apply operating-system process memory/CPU limits and a
bounded-output supervisor. OCR output is untrusted screen content and must
never be treated as instructions for agents or tools. It is data to interpret,
not authority to execute.

The adapter extracts text only; it does not understand visual layout, icons,
application state or diagrams. Those are the next Yuki Vision tasks.
