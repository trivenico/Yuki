"""Windows foreground-application metadata collector.

Only an allowlisted executable-to-application-ID mapping is emitted. Window
titles, full paths, process arguments, screenshots, clipboard and input are
never exposed to the observation pipeline.
"""
from __future__ import annotations

import ntpath
import re
import sys
from typing import Callable, Optional

from core.desktop_metadata_adapter import DesktopMetadataAdapter


_EXECUTABLE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}\.exe\Z")


def _read_windows_foreground_executable() -> Optional[str]:
    """Return only the foreground process executable basename on Windows."""
    if sys.platform != "win32":
        raise RuntimeError("Windows foreground metadata is unavailable on this host")
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
        return None
    process = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return None
        return ntpath.basename(buffer.value).lower()
    finally:
        kernel32.CloseHandle(process)


class WindowsForegroundAppCollector:
    """Explicitly started, indicator-bound, caller-polled Windows collector."""

    platform = "windows"

    def __init__(
        self,
        executable_to_app_id: dict[str, str],
        *,
        reader: Optional[Callable[[], Optional[str]]] = None,
    ):
        if not isinstance(executable_to_app_id, dict) or len(executable_to_app_id) > 128:
            raise ValueError("executable_to_app_id must be a bounded mapping")
        mapping = {}
        for executable, app_id in executable_to_app_id.items():
            if not isinstance(executable, str) or not _EXECUTABLE.fullmatch(executable.lower()):
                raise ValueError("mapping keys must be executable basenames ending in .exe")
            if not isinstance(app_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,127}", app_id.lower()):
                raise ValueError("mapping values must be bounded application identifiers")
            mapping[executable.lower()] = app_id.lower()
        self._mapping = mapping
        self._reader = reader or _read_windows_foreground_executable
        self._sink = None
        self._indicator = None
        self._active = False

    def start(
        self,
        sink: DesktopMetadataAdapter,
        *,
        consent_granted: bool,
        indicator: Callable[[bool], None],
    ) -> None:
        if self._active:
            raise RuntimeError("collector is already active")
        if not isinstance(sink, DesktopMetadataAdapter):
            raise TypeError("sink must be a DesktopMetadataAdapter")
        if consent_granted is not True:
            raise PermissionError("explicit desktop observation consent is required")
        if not callable(indicator):
            raise ValueError("a visible indicator callback is required")
        # Do not mark active unless the UI successfully displays the indicator.
        indicator(True)
        self._sink = sink
        self._indicator = indicator
        self._active = True

    def poll_once(self) -> bool:
        """Sample one foreground executable; no background thread is created."""
        if not self._active or self._sink is None:
            return False
        executable = self._reader()
        if not isinstance(executable, str):
            return False
        normalized = ntpath.basename(executable).lower()
        app_id = self._mapping.get(normalized)
        if app_id is None:
            return False
        return self._sink.publish_app_activity(app_id)

    def stop(self) -> None:
        if self._active:
            indicator = self._indicator
            self._active = False
            self._sink = None
            self._indicator = None
            if indicator is not None:
                indicator(False)
