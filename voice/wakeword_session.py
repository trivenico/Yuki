"""Opt-in local wake-word session controller.

This module owns lifecycle and privacy boundaries, not a particular wake-word
engine or microphone driver. Injected adapters must process raw PCM locally;
the controller never writes frames to disk or sends them over a network.
"""
from __future__ import annotations

from threading import Event, RLock, Thread, current_thread
from typing import Callable


class WakeWordError(RuntimeError):
    """A local wake-word session could not safely start or continue."""


class WakeWordSession:
    """One-shot, explicitly started local wake-word listener.

    The audio source contract is:
      - start(): open the microphone only when called
      - read(): return one bytes-like mono PCM16 frame, or None while stopping
      - stop(): interrupt a blocked read
      - close(): release the device

    The detector contract is:
      - process(frame: bytes) -> bool; perform local inference only
      - close(): release model/runtime resources

    on_wake must enqueue a wake event for the application. It must not perform
    transcription or other long-running work on the audio thread.
    """

    MAX_FRAME_BYTES = 16 * 1024
    JOIN_TIMEOUT_SECONDS = 3.0

    def __init__(
        self,
        *,
        audio_source_factory: Callable,
        detector_factory: Callable,
        on_wake: Callable,
        on_state: Callable | None = None,
        on_error: Callable | None = None,
    ) -> None:
        for name, callback in (
            ("audio_source_factory", audio_source_factory),
            ("detector_factory", detector_factory),
            ("on_wake", on_wake),
        ):
            if not callable(callback):
                raise TypeError(f"{name} must be callable.")
        if on_state is not None and not callable(on_state):
            raise TypeError("on_state must be callable or None.")
        if on_error is not None and not callable(on_error):
            raise TypeError("on_error must be callable or None.")

        self._audio_source_factory = audio_source_factory
        self._detector_factory = detector_factory
        self._on_wake = on_wake
        self._on_state = on_state
        self._on_error = on_error
        self._lock = RLock()
        self._stop_event = Event()
        self._source = None
        self._detector = None
        self._thread = None
        self._state = "disabled"
        self._closed = False
        self._generation = 0

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def listening(self) -> bool:
        return self.state == "listening"

    def _notify_state(self, state: str) -> None:
        callback = self._on_state
        if callback is not None:
            try:
                callback(state)
            except Exception:
                # UI observers must not destabilize microphone lifecycle.
                pass

    def _set_state(self, state: str) -> None:
        with self._lock:
            if self._closed and state != "closed":
                return
            if self._state == state:
                return
            self._state = state
        self._notify_state(state)

    @staticmethod
    def _validate_source(source) -> None:
        for method in ("start", "read", "stop", "close"):
            if not callable(getattr(source, method, None)):
                raise TypeError(f"audio source must provide {method}().")

    @staticmethod
    def _validate_detector(detector) -> None:
        for method in ("process", "close"):
            if not callable(getattr(detector, method, None)):
                raise TypeError(f"wake-word detector must provide {method}().")

    def start(self) -> None:
        """Start only after an explicit user action; never called at construction."""
        with self._lock:
            if self._closed:
                raise WakeWordError("This wake-word session is closed.")
            if self._state in {"starting", "listening", "stopping"}:
                raise WakeWordError("The wake-word session is already active.")
            if self._thread is not None and self._thread.is_alive():
                raise WakeWordError("The previous wake-word worker has not stopped.")
            self._stop_event.clear()
            self._generation += 1
            generation = self._generation
            self._state = "starting"
        self._notify_state("starting")

        detector = None
        source = None
        try:
            # Construct the detector before opening the microphone. No adapter
            # or model is loaded until this explicit start() call.
            detector = self._detector_factory()
            self._validate_detector(detector)
            with self._lock:
                if self._closed or self._stop_event.is_set() or generation != self._generation:
                    raise WakeWordError("Wake-word startup was cancelled.")
                self._detector = detector

            source = self._audio_source_factory()
            self._validate_source(source)
            with self._lock:
                if self._closed or self._stop_event.is_set() or generation != self._generation:
                    raise WakeWordError("Wake-word startup was cancelled.")
                self._source = source

            # The visible "starting" state is emitted before opening the device.
            source.start()
            with self._lock:
                if self._closed or self._stop_event.is_set() or generation != self._generation:
                    raise WakeWordError("Wake-word startup was cancelled.")
                self._state = "listening"
                worker = Thread(
                    target=self._listen,
                    args=(generation, source, detector),
                    name="yuki-wake-word",
                    daemon=True,
                )
                self._thread = worker
            self._notify_state("listening")
            worker.start()
        except Exception as exc:
            self._release(source, detector)
            with self._lock:
                if self._source is source:
                    self._source = None
                if self._detector is detector:
                    self._detector = None
                cancelled = self._closed or self._stop_event.is_set() or generation != self._generation
                if not self._closed and generation == self._generation:
                    self._state = "disabled" if cancelled else "failed"
                next_state = "closed" if self._closed else ("disabled" if cancelled else "failed")
            self._notify_state(next_state)
            if isinstance(exc, WakeWordError):
                raise
            if not cancelled:
                self._report_error(exc)
            raise WakeWordError("Local wake-word listener could not start safely.") from exc

    def _report_error(self, error: Exception) -> None:
        if self._on_error is not None:
            try:
                self._on_error(error)
            except Exception:
                pass

    def _release(self, source, detector) -> None:
        if source is not None:
            try:
                source.stop()
            except Exception:
                pass
            try:
                source.close()
            except Exception:
                pass
        if detector is not None:
            try:
                detector.close()
            except Exception:
                pass

    def _listen(self, generation: int, source, detector) -> None:
        detected = False
        failure = None
        try:
            while not self._stop_event.is_set():
                frame = source.read()
                if self._stop_event.is_set():
                    break
                if frame is None:
                    continue
                if not isinstance(frame, (bytes, bytearray, memoryview)):
                    raise WakeWordError("Audio source returned a non-bytes PCM frame.")
                if not 0 < len(frame) <= self.MAX_FRAME_BYTES:
                    raise WakeWordError("Audio source returned an empty or oversized PCM frame.")
                # Frame bytes are passed directly to the local detector and are
                # not appended to a queue, file, log, transcript, or memory.
                if detector.process(frame) is True:
                    detected = True
                    break
        except Exception as exc:
            if not self._stop_event.is_set():
                failure = exc
        finally:
            self._release(source, detector)
            with self._lock:
                if self._source is source:
                    self._source = None
                if self._detector is detector:
                    self._detector = None
                if self._thread is current_thread():
                    self._thread = None
                closed = self._closed
                cancelled = self._stop_event.is_set()
                if closed:
                    next_state = "closed"
                elif failure is not None:
                    next_state = "failed"
                elif detected:
                    next_state = "triggered"
                else:
                    next_state = "disabled"
                self._state = next_state
            self._notify_state(next_state)

        if failure is not None:
            self._report_error(failure)
        elif detected and not self._stop_event.is_set():
            try:
                self._on_wake()
            except Exception as exc:
                self._report_error(exc)

    def stop(self, timeout: float | None = None) -> bool:
        """Stop capture and wait briefly for the audio worker to release devices."""
        if timeout is None:
            timeout = self.JOIN_TIMEOUT_SECONDS
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
            raise ValueError("timeout must be a non-negative number.")
        with self._lock:
            if self._closed:
                return True
            self._stop_event.set()
            source = self._source
            worker = self._thread
            if self._state not in {"disabled", "failed", "triggered"}:
                self._state = "stopping"
                notify = True
            else:
                notify = False
        if notify:
            self._notify_state("stopping")
        if source is not None:
            try:
                source.stop()
            except Exception as exc:
                self._report_error(exc)
        if worker is not None and worker is not current_thread():
            worker.join(timeout)
        stopped = worker is None or not worker.is_alive()
        if stopped:
            with self._lock:
                if not self._closed and self._state != "failed":
                    self._state = "disabled"
                    notify_disabled = True
                else:
                    notify_disabled = False
            if notify_disabled:
                self._notify_state("disabled")
        return stopped

    def close(self) -> bool:
        """Terminal shutdown; no automatic restart or reactivation is possible."""
        stopped = self.stop()
        with self._lock:
            self._closed = True
            self._state = "closed"
        self._notify_state("closed")
        return stopped
