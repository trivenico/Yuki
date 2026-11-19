"""Platform-neutral contract and capability reporting for desktop collectors.

Detection is informational only: it does not request OS permissions, start
capture, or imply that a native collector is installed.
"""
from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Callable, Protocol, runtime_checkable

from core.desktop_metadata_adapter import DesktopMetadataAdapter


_SUPPORTED_PLATFORMS = frozenset({"windows", "macos", "linux"})


@dataclass(frozen=True)
class DesktopPlatformInfo:
    platform: str
    supported: bool
    native_collector_available: bool
    permission_state: str
    supported_signals: tuple[str, ...]


def detect_desktop_platform(platform_name: str | None = None) -> DesktopPlatformInfo:
    """Identify the host OS without probing permissions or collecting data."""
    raw = sys.platform if platform_name is None else platform_name
    if not isinstance(raw, str):
        raise ValueError("platform_name must be text")
    if raw == "win32":
        name = "windows"
    elif raw == "darwin":
        name = "macos"
    elif raw.startswith("linux"):
        name = "linux"
    else:
        name = "unsupported"
    return DesktopPlatformInfo(
        platform=name,
        supported=name in _SUPPORTED_PLATFORMS,
        # Windows has a caller-polled foreground-app collector; other native
        # platform backends remain unavailable.
        native_collector_available=name == "windows",
        permission_state="not_requested",
        supported_signals=("allowlisted_foreground_application_id",) if name == "windows" else (),
    )


@runtime_checkable
class DesktopCollector(Protocol):
    """Native collector contract; implementations must remain metadata-only.

    A collector receives an already configured adapter. It must not broaden
    the adapter allowlists, retain raw content, or request permissions itself.
    Permission acquisition belongs to the explicit desktop UI flow.
    """

    @property
    def platform(self) -> str:
        ...

    def start(
        self,
        sink: DesktopMetadataAdapter,
        *,
        consent_granted: bool,
        indicator: Callable[[bool], None],
    ) -> None:
        """Start only after explicit consent and visible-indicator activation."""
        ...

    def stop(self) -> None:
        """Stop collection and release OS resources."""
        ...


class UnsupportedDesktopCollector:
    """Fail-closed placeholder used until a native platform backend exists."""

    def __init__(self, platform: str):
        if platform not in _SUPPORTED_PLATFORMS:
            raise ValueError("unsupported desktop platform")
        self._platform = platform

    @property
    def platform(self) -> str:
        return self._platform

    def start(
        self,
        sink: DesktopMetadataAdapter,
        *,
        consent_granted: bool,
        indicator: Callable[[bool], None],
    ) -> None:
        if not isinstance(sink, DesktopMetadataAdapter):
            raise TypeError("sink must be a DesktopMetadataAdapter")
        raise RuntimeError(f"native {self._platform} desktop collector is not implemented")

    def stop(self) -> None:
        # Safe and idempotent: no native resources are acquired by this stub.
        return None
