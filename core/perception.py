"""Explicit, local-only desktop perception primitives.

No background capture, recording, networking, OCR, or image upload is performed
here. The desktop UI must pass an enabled permission before invoking capture.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any


class PerceptionUnavailable(RuntimeError):
    """A requested capture backend or platform feature is unavailable."""


class PerceptionPermissionDenied(PermissionError):
    """The relevant user-controlled setting is disabled."""


@dataclass(frozen=True)
class ScreenContext:
    active_window: str | None
    platform: str

    def as_prompt_context(self) -> str:
        title = self.active_window or "Unavailable"
        return (
            "User-authorized local desktop context (metadata only; may be incomplete):\n"
            f"Platform: {self.platform}\nActive window title: {title}\n"
            "Do not infer private content that was not provided. Ask before taking actions."
        )


def require_permission(enabled: bool, capability: str) -> None:
    if enabled is not True:
        raise PerceptionPermissionDenied(
            f"{capability} is disabled. Enable it explicitly in Yuki Settings first."
        )


def capture_screen(*, enabled: bool) -> Any:
    """Capture one screenshot in memory. It is not saved or transmitted."""
    require_permission(enabled, "Screen observation")
    try:
        from PIL import ImageGrab
    except ImportError as exc:
        raise PerceptionUnavailable(
            "Screen capture needs the optional Pillow package. Install Pillow to enable it."
        ) from exc
    try:
        image = ImageGrab.grab()
    except Exception as exc:
        raise PerceptionUnavailable(
            f"Screen capture failed ({type(exc).__name__}). Check OS screen-capture permissions."
        ) from exc
    if image.width < 1 or image.height < 1:
        raise PerceptionUnavailable("The operating system returned an empty screenshot.")
    return image


def capture_camera_frame(*, enabled: bool, camera_index: int = 0) -> Any:
    """Capture one frame and release the camera immediately; no recording."""
    require_permission(enabled, "Camera access")
    if isinstance(camera_index, bool) or not isinstance(camera_index, int) or not 0 <= camera_index <= 4:
        raise ValueError("camera_index must be an integer between 0 and 4.")
    try:
        import cv2
        from PIL import Image
    except ImportError as exc:
        raise PerceptionUnavailable(
            "Camera preview needs optional OpenCV and Pillow packages."
        ) from exc

    device = None
    try:
        device = cv2.VideoCapture(camera_index)
        if not device.isOpened():
            raise PerceptionUnavailable(
                "Camera is unavailable or permission was denied. Check OS privacy settings."
            )
        ok, frame = device.read()
        if not ok or frame is None:
            raise PerceptionUnavailable("The camera did not provide a frame.")
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
    except PerceptionUnavailable:
        raise
    except Exception as exc:
        raise PerceptionUnavailable(f"Camera capture failed ({type(exc).__name__}).") from exc
    finally:
        if device is not None:
            try:
                device.release()
            except Exception:
                pass


def active_window_title() -> str | None:
    """Return only the active window title where a safe native API is available."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(min(length + 1, 2049))
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        title = buffer.value.strip()
        return title[:2048] or None
    except Exception:
        return None


def get_screen_context(*, enabled: bool) -> ScreenContext:
    """Read minimal foreground-window metadata after explicit opt-in."""
    require_permission(enabled, "Screen observation")
    return ScreenContext(active_window=active_window_title(), platform=sys.platform)
