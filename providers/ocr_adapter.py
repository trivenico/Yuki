"""Bounded local Tesseract OCR adapter for authorized Yuki screen frames.

OCR is performed locally. This adapter does not upload, persist, or log screen
pixels or extracted text. The caller remains responsible for displaying the
result safely and for applying sensitive-content handling.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import subprocess

from providers.screen_observation_policy import ScreenObservationGrant
from providers.screen_transport import validate_screen_frame


class OCRProcessingError(RuntimeError):
    """Local OCR could not safely process the authorized screen frame."""


@dataclass(frozen=True)
class OCRResult:
    text: str
    language: str
    engine: str
    image_width: int
    image_height: int


_LANGUAGE = re.compile(r"^[a-zA-Z_]{2,16}$")
MAX_OCR_TEXT_BYTES = 64 * 1024
MAX_OCR_TIMEOUT_SECONDS = 20
DEFAULT_OCR_TIMEOUT_SECONDS = 12


def extract_screen_text(
    image_bytes: bytes,
    *,
    grant: ScreenObservationGrant,
    owner_id: str,
    device_id: str,
    session_id: str,
    transport: str,
    now_epoch: int,
    language: str = "eng",
    timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS,
    runner=subprocess.run,
    executable_lookup=shutil.which,
) -> OCRResult:
    """Run Tesseract against an explicitly authorized PNG frame using stdin."""
    if not isinstance(language, str) or not _LANGUAGE.fullmatch(language):
        raise OCRProcessingError("invalid_ocr_language")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_OCR_TIMEOUT_SECONDS:
        raise OCRProcessingError("invalid_ocr_timeout")
    frame = validate_screen_frame(
        image_bytes,
        grant=grant,
        owner_id=owner_id,
        device_id=device_id,
        session_id=session_id,
        transport=transport,
        now_epoch=now_epoch,
    )
    executable = executable_lookup("tesseract")
    if not isinstance(executable, str) or not executable.startswith("/"):
        raise OCRProcessingError("local_tesseract_not_installed")
    command = [
        executable, "stdin", "stdout", "-l", language,
        "--psm", "6",
    ]
    try:
        completed = runner(
            command,
            input=image_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OCRProcessingError("ocr_timed_out") from exc
    except OSError as exc:
        raise OCRProcessingError("ocr_execution_failed") from exc
    if completed.returncode != 0:
        raise OCRProcessingError("ocr_engine_failed")
    output = completed.stdout
    if not isinstance(output, bytes) or len(output) > MAX_OCR_TEXT_BYTES:
        raise OCRProcessingError("ocr_output_invalid_or_too_large")
    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OCRProcessingError("ocr_output_not_utf8") from exc
    return OCRResult(
        text=text,
        language=language,
        engine="tesseract",
        image_width=frame.width,
        image_height=frame.height,
    )
