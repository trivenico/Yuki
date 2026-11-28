# Desktop Speech Backend Cleanup Retention Audit

## Finding

**Severity: Reliability / resource lifecycle defect**

The native desktop can discard its last application-owned reference to a speech-output session after backend cleanup fails. The speech output is created per authenticated desktop session and is not registered with `DesktopServiceLifecycle`, so the main service lifecycle cannot retry its cleanup.

This is a concrete resource-retention defect. Whether a particular synthesizer failure leaves a native audio/device resource allocated depends on the backend implementation and failure point; this audit does not claim a production leak was observed.

## Evidence

### 1. Speech output is a separately managed session

`ui/desktop/launch.py` stores the optional output in `self.speech_session`. It is created by `create_desktop_voice_output()` and is not part of the resource tuple passed to `DesktopServiceLifecycle` in `_build_services()`.

### 2. Inactive playback cleanup failure loses the reference

In `_set_spoken_replies_enabled(False)`, the code first assigns the session to a local variable and sets `self.speech_session = None`. It then calls `_retire_speech_session()`. When no playback worker is active, that helper calls `session.close()`; on failure it returns `True`, but the caller only returns from the handler and does not retain the failed session for retry.

The same detach-before-close pattern appears in `_logout()` and `closeEvent()`. Both call `_retire_speech_session()` after clearing `self.speech_session`, and neither retains a failed inactive-session close.

### 3. Active playback cleanup failure is also discarded

When a speech worker is active, retirement intentionally defers backend closure until playback finishes. `_spoken_reply_finished()` detects that the captured session is no longer the current session and invokes `_close_retired_speech_session()`.

That helper returns the close exception, but the callback ignores the return value. There is no retry collection or lifecycle registration for the failed session.

### 4. Backend close can fail

`voice/output_session.py` implements `VoiceOutputSession.close()` by invoking the underlying synthesizer's `close()` method (or `stop()` fallback). It catches backend exceptions and raises `VoiceOutputError`. Thus the cleanup failure handled above is an explicit supported failure path, not an impossible condition.

## Impact and boundaries

- A failed speech-backend close can leave native/backend resources unclosed.
- The desktop may continue sign-out or shutdown without retaining a handle for another cleanup attempt.
- The defect is isolated to optional spoken-reply resources; it does not establish a failure in the main desktop lifecycle or provider shutdown.
- The exact resource impact depends on the configured TTS backend and its close failure behavior.
- No production leak or exploit was observed as part of this source review.

## Recommended remediation

1. Track retired speech sessions in an owner-managed collection until close succeeds.
2. Make the retirement helper return the failed session (or a structured result containing it), not only a boolean.
3. Have both immediate and deferred playback completion paths retain failed sessions and expose a retry path.
4. During sign-out and application shutdown, do not report cleanup complete while retired speech sessions remain unclosed; retry them on subsequent cleanup attempts.
5. Preserve the existing safety rule: do not close a synthesizer while its playback worker is still using it.
6. Add fault-injection tests for close failures with playback active and inactive, covering disable, sign-out, window close, retry, and successful final cleanup.

## Verification status

- Source reviewed: `ui/desktop/launch.py`, `voice/output_session.py`, `voice/desktop_output.py`.
- No runtime code changed.
- No tests executed.
- No production resource leak was observed.
