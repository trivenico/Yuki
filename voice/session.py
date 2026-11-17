"""Explicit, bounded desktop voice capture with ephemeral audio storage.

This adapter never opens a microphone at construction time. A caller must
explicitly enable the session and invoke capture_and_transcribe(). Audio is
written only to a private temporary directory and removed after transcription.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, RLock

from voice.stt import FasterWhisperSTT, STTError


class VoiceSessionError(RuntimeError):
    """A voice session could not safely complete."""


class VoiceSession:
    """One opt-in voice session; no background listener or wake-word loop."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        stt=None,
    ) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean.")
        self._stt = stt or FasterWhisperSTT(
            enabled=enabled,
            model_size=model_size,
            device=device,
            compute_type=compute_type,
        )
        if not callable(getattr(self._stt, "record_wav", None)) or not callable(
            getattr(self._stt, "transcribe_file", None)
        ):
            raise TypeError("stt must provide record_wav() and transcribe_file().")
        self._stt.enabled = enabled
        self._enabled = enabled
        self._closed = False
        self._cancel = Event()
        self._state_lock = RLock()
        self._operation_lock = Lock()

    @property
    def enabled(self) -> bool:
        with self._state_lock:
            return self._enabled and not self._closed

    def enable(self) -> None:
        with self._state_lock:
            if self._closed:
                raise VoiceSessionError("This voice session is closed.")
            self._enabled = True
            self._stt.enabled = True

    def disable(self) -> None:
        """Revoke microphone consent and stop any active bounded capture."""
        with self._state_lock:
            self._enabled = False
            self._stt.enabled = False
            self._cancel.set()

    def cancel(self) -> None:
        """Stop an active microphone capture; transcription already underway may finish."""
        self._cancel.set()

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            self._enabled = False
            self._stt.enabled = False
            self._cancel.set()

    def capture_and_transcribe(
        self,
        *,
        seconds: int = 5,
        language: str | None = None,
    ) -> str:
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 10:
            raise ValueError("Voice capture duration must be 1-10 seconds.")
        if not self._operation_lock.acquire(blocking=False):
            raise VoiceSessionError("A voice capture is already in progress.")
        try:
            with self._state_lock:
                if self._closed or not self._enabled:
                    raise VoiceSessionError("Microphone access is not enabled for this session.")
                self._cancel.clear()

            # TemporaryDirectory is private to the current user on POSIX. The
            # recording helper also creates the file exclusively with 0600
            # permissions where supported and refuses symlink/overwrite paths.
            with TemporaryDirectory(prefix="yuki-voice-") as directory:
                audio_path = Path(directory) / "capture.wav"
                self._stt.record_wav(
                    audio_path,
                    seconds=seconds,
                    sample_rate=16000,
                    cancellation_event=self._cancel,
                )
                if self._cancel.is_set():
                    raise VoiceSessionError("Voice capture was cancelled.")
                result = self._stt.transcribe_file(audio_path, language=language)
                if self._cancel.is_set():
                    raise VoiceSessionError("Voice session was stopped.")
                if not isinstance(result, dict):
                    raise VoiceSessionError("Speech recognition returned an invalid result.")
                transcript = result.get("text")
                if not isinstance(transcript, str):
                    raise VoiceSessionError("Speech recognition returned invalid text.")
                transcript = transcript.strip()
                if len(transcript) > 20000:
                    raise VoiceSessionError("Speech recognition returned too much text.")
                return transcript
        except VoiceSessionError:
            raise
        except STTError as exc:
            raise VoiceSessionError(str(exc)) from exc
        except Exception as exc:
            raise VoiceSessionError("Voice capture or transcription failed safely.") from exc
        finally:
            self._operation_lock.release()
