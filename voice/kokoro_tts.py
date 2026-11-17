"""Optional offline Kokoro TTS adapter using sherpa-onnx.

The adapter is deliberately lazy: construction validates configuration only.
It does not import sherpa-onnx, load model weights, open an audio device, or
download assets until an explicitly enabled speak() call.
"""
from __future__ import annotations

import math
import threading
import time
from pathlib import Path

_MAX_CHUNKS = 100
_MAX_TOTAL_CHARACTERS = 20_000
_MAX_CHUNK_CHARACTERS = 2_000
_MAX_AUDIO_SECONDS = 120
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_MIN_SAMPLE_RATE = 8_000
_MAX_SAMPLE_RATE = 96_000

# Speaker IDs documented by the official sherpa-onnx kokoro-en-v0_19 pack.
KOKORO_ENGLISH_VOICES = {
    "af_bella": 1,
    "af_nicole": 2,
    "af_sarah": 3,
    "af_sky": 4,
    "bf_emma": 7,
    "bf_isabella": 8,
}


class KokoroTTSError(RuntimeError):
    """Safe, user-displayable Kokoro adapter error."""


class KokoroTTS:
    """Lazy, offline Kokoro synthesizer with bounded PCM playback.

    Speech generation is synchronous and must be called from a background
    worker. stop() immediately stops playback and prevents generated audio
    from starting, but sherpa-onnx's synchronous generate() call itself is
    not forcibly interruptible.
    """

    backend = "kokoro"

    def __init__(
        self,
        *,
        model_dir,
        lexicon_path=None,
        voice="af_bella",
        speed=1.0,
        enabled=False,
        num_threads=2,
        playback_watchdog_seconds=125,
    ):
        if voice not in KOKORO_ENGLISH_VOICES:
            raise ValueError("voice must be a supported Kokoro English preset.")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2.0:
            raise ValueError("speed must be between 0.5 and 2.0.")
        if isinstance(num_threads, bool) or not isinstance(num_threads, int) or not 1 <= num_threads <= 8:
            raise ValueError("num_threads must be between 1 and 8.")
        if (
            isinstance(playback_watchdog_seconds, bool)
            or not isinstance(playback_watchdog_seconds, (int, float))
            or not 1 <= playback_watchdog_seconds <= 130
        ):
            raise ValueError("playback_watchdog_seconds must be between 1 and 130.")
        self.model_dir = Path(model_dir).expanduser().resolve()
        self.lexicon_path = Path(lexicon_path).expanduser().resolve() if lexicon_path else None
        self.voice = voice
        self.speed = float(speed)
        self.enabled = enabled is True
        self.num_threads = num_threads
        self.playback_watchdog_seconds = float(playback_watchdog_seconds)
        self._tts = None
        self._load_lock = threading.Lock()
        self._operation_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._generation = 0

    def _asset_paths(self):
        if not self.model_dir.is_dir():
            raise KokoroTTSError("Configure an installed local Kokoro model directory.")
        model = self.model_dir / "model.onnx"
        voices = self.model_dir / "voices.bin"
        tokens = self.model_dir / "tokens.txt"
        for path in (model, voices, tokens):
            if not path.is_file():
                raise KokoroTTSError("The local Kokoro model is incomplete.")
        if self.lexicon_path is not None and not self.lexicon_path.is_file():
            raise KokoroTTSError("The configured Kokoro English lexicon file is missing.")
        return model, voices, tokens

    def _load(self):
        if self._tts is not None:
            return self._tts
        with self._load_lock:
            if self._tts is not None:
                return self._tts
            model, voices, tokens = self._asset_paths()
            try:
                import sherpa_onnx
            except ImportError as exc:
                raise KokoroTTSError(
                    "Install the optional Kokoro runtime requirements to use this voice."
                ) from exc
            try:
                kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
                    model=str(model),
                    voices=str(voices),
                    tokens=str(tokens),
                    lexicon=str(self.lexicon_path) if self.lexicon_path else "",
                )
                model_config = sherpa_onnx.OfflineTtsModelConfig(
                    kokoro=kokoro,
                    num_threads=self.num_threads,
                    debug=False,
                )
                config = sherpa_onnx.OfflineTtsConfig(model=model_config)
                tts = sherpa_onnx.OfflineTts(config)
                if not getattr(tts, "is_ready", True):
                    raise KokoroTTSError("The local Kokoro model failed its readiness check.")
            except KokoroTTSError:
                raise
            except Exception as exc:
                raise KokoroTTSError("The local Kokoro model could not be loaded.") from exc
            self._tts = tts
            return tts

    def stop(self):
        """Stop active playback and invalidate audio not yet played."""
        self._stop_event.set()
        self._generation += 1
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def close(self):
        """Release the cached model reference; safe to call repeatedly."""
        self.stop()
        with self._load_lock:
            self._tts = None

    def _validate_audio(self, audio):
        try:
            import numpy as np
        except ImportError as exc:
            raise KokoroTTSError("Install the optional NumPy dependency for Kokoro playback.") from exc
        samples = np.asarray(audio.samples)
        sample_rate = audio.sample_rate
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
            raise KokoroTTSError("Kokoro returned an invalid sample rate.")
        if not _MIN_SAMPLE_RATE <= sample_rate <= _MAX_SAMPLE_RATE:
            raise KokoroTTSError("Kokoro audio sample rate is outside the supported safety range.")
        if samples.dtype.kind not in "fi" or samples.ndim not in (1, 2):
            raise KokoroTTSError("Kokoro returned an unsupported audio format.")
        if samples.ndim == 2 and samples.shape[1] not in (1, 2):
            raise KokoroTTSError("Kokoro audio has too many channels.")
        if samples.size == 0 or not np.isfinite(samples).all():
            raise KokoroTTSError("Kokoro returned empty or non-finite audio.")
        if samples.nbytes > _MAX_AUDIO_BYTES:
            raise KokoroTTSError("Kokoro audio exceeds the 25 MiB safety limit.")
        duration = samples.shape[0] / sample_rate
        if not math.isfinite(duration) or duration <= 0 or duration > _MAX_AUDIO_SECONDS:
            raise KokoroTTSError("Kokoro audio exceeds the 120-second duration limit.")
        return samples.astype("float32", copy=False), sample_rate

    def _play(self, samples, sample_rate, generation):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise KokoroTTSError("Install the optional sounddevice dependency for audio playback.") from exc
        if self._stop_event.is_set() or generation != self._generation:
            raise KokoroTTSError("Speech was stopped before playback.")
        deadline = time.monotonic() + self.playback_watchdog_seconds
        try:
            sd.play(samples, sample_rate, blocking=False)
            while True:
                if self._stop_event.is_set() or generation != self._generation:
                    sd.stop()
                    raise KokoroTTSError("Speech was interrupted.")
                stream = sd.get_stream()
                if stream is None or not stream.active:
                    return
                if time.monotonic() >= deadline:
                    sd.stop()
                    raise KokoroTTSError("Kokoro playback exceeded its watchdog limit.")
                self._stop_event.wait(0.05)
        except KokoroTTSError:
            raise
        except Exception as exc:
            try:
                sd.stop()
            except Exception:
                pass
            raise KokoroTTSError("Kokoro audio playback failed safely.") from exc

    def speak(self, text):
        """Generate and play one bounded text segment; call from a worker."""
        self._stop_event.clear()
        generation = self._generation
        return self._speak(text, generation)

    def _speak(self, text, generation):
        if not self.enabled:
            raise KokoroTTSError("Voice output is disabled.")
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_CHUNK_CHARACTERS:
            raise ValueError("Text must be 1-2000 characters.")
        if not self._operation_lock.acquire(blocking=False):
            raise KokoroTTSError("Kokoro is already processing a speech request.")
        try:
            if self._stop_event.is_set() or generation != self._generation:
                raise KokoroTTSError("Speech was stopped before synthesis.")
            tts = self._load()
            if self._stop_event.is_set() or generation != self._generation:
                raise KokoroTTSError("Speech was stopped before synthesis.")
            try:
                import sherpa_onnx
                config = sherpa_onnx.GenerationConfig()
                config.sid = KOKORO_ENGLISH_VOICES[self.voice]
                config.speed = self.speed
                audio = tts.generate(text, config)
            except Exception as exc:
                if isinstance(exc, KokoroTTSError):
                    raise
                raise KokoroTTSError("Kokoro speech generation failed safely.") from exc
            if self._stop_event.is_set() or generation != self._generation:
                raise KokoroTTSError("Speech was stopped during synthesis.")
            samples, sample_rate = self._validate_audio(audio)
            self._play(samples, sample_rate, generation)
        finally:
            self._operation_lock.release()

    def speak_stream(self, chunks):
        """Validate the full bounded stream before producing any audio."""
        validated = []
        total = 0
        for chunk in chunks:
            if not isinstance(chunk, str):
                raise ValueError("Speech chunks must be text.")
            if not chunk.strip():
                continue
            if len(chunk) > _MAX_CHUNK_CHARACTERS:
                raise ValueError("Each speech chunk must be at most 2000 characters.")
            total += len(chunk)
            if len(validated) >= _MAX_CHUNKS:
                raise ValueError("Speech stream exceeds the 100-chunk limit.")
            if total > _MAX_TOTAL_CHARACTERS:
                raise ValueError("Speech stream exceeds the 20000-character limit.")
            validated.append(chunk)
        self._stop_event.clear()
        generation = self._generation
        spoken = 0
        # Hold the re-entrant operation lock for the entire stream so another
        # caller cannot interleave unrelated speech between consecutive chunks.
        if not self._operation_lock.acquire(blocking=False):
            raise KokoroTTSError("Kokoro is already processing a speech request.")
        try:
            for chunk in validated:
                if self._stop_event.is_set() or generation != self._generation:
                    break
                self._speak(chunk, generation)
                spoken += 1
            return {
                "chunks_spoken": spoken,
                "interrupted": self._stop_event.is_set() or generation != self._generation,
            }
        finally:
            self._operation_lock.release()
