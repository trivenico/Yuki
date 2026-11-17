"""Bounded, in-memory microphone frames for local wake-word detection.

No recording files, network clients, transcripts, or persistent buffers are
created. sounddevice is imported only when start() is explicitly called.
"""
from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Event, Lock
import time


class WakeWordAudioError(RuntimeError):
    """The local microphone source could not be opened or read safely."""


class SoundDeviceWakeWordAudioSource:
    """Capture 80 ms mono PCM16 frames into a one-frame volatile queue.

    The default format is 16 kHz, mono, signed PCM16. Device support varies;
    a device that cannot open this exact format fails closed rather than
    silently changing the detector's expected input.
    """

    SAMPLE_RATE = 16000
    CHANNELS = 1
    BLOCKSIZE = 1280  # 80 ms at 16 kHz
    FRAME_BYTES = BLOCKSIZE * 2
    QUEUE_READ_TIMEOUT_SECONDS = 0.1

    def __init__(self, *, sounddevice_module=None) -> None:
        self._sounddevice = sounddevice_module
        self._stream = None
        self._frames: Queue[bytes] = Queue(maxsize=1)
        self._stop_event = Event()
        self._lock = Lock()
        self._started = False
        self._closed = False

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise WakeWordAudioError("The microphone source is closed.")
            if self._started:
                raise WakeWordAudioError("The microphone source is already started.")
            self._stop_event.clear()

        sd = self._sounddevice
        if sd is None:
            try:
                import sounddevice as sd
            except ImportError as exc:
                raise WakeWordAudioError(
                    "Install the optional desktop voice dependencies to use a microphone."
                ) from exc

        def on_audio(indata, frames, time_info, status):
            if self._stop_event.is_set():
                return
            # Keep only the newest bounded frame; never build an audio history.
            frame = bytes(indata)
            if len(frame) != self.FRAME_BYTES:
                return
            try:
                self._frames.put_nowait(frame)
            except Full:
                try:
                    self._frames.get_nowait()
                except Empty:
                    pass
                try:
                    self._frames.put_nowait(frame)
                except Full:
                    pass

        stream = None
        try:
            stream = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype="int16",
                blocksize=self.BLOCKSIZE,
                callback=on_audio,
            )
            stream.start()
        except Exception as exc:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise WakeWordAudioError(
                "Could not open the microphone in 16 kHz mono PCM16 mode."
            ) from exc

        with self._lock:
            if self._closed or self._stop_event.is_set():
                try:
                    stream.stop()
                finally:
                    stream.close()
                raise WakeWordAudioError("Microphone startup was cancelled.")
            self._sounddevice = sd
            self._stream = stream
            self._started = True

    def read(self):
        while not self._stop_event.is_set():
            try:
                return self._frames.get(timeout=self.QUEUE_READ_TIMEOUT_SECONDS)
            except Empty:
                continue
        return None

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            stream = self._stream
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
        self._discard_pending_frame()

    def _discard_pending_frame(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except Empty:
                return

    def close(self) -> None:
        self.stop()
        with self._lock:
            stream = self._stream
            self._stream = None
            self._started = False
            self._closed = True
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        self._discard_pending_frame()
