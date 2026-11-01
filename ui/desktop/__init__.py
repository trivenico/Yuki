"""Optional native desktop client boundary.

Importing this package never imports Qt. CLI and server users can safely keep
using Yuki without installing the desktop extra.
"""
from ui.desktop.auth import DesktopSessionManager, load_os_keyring
from ui.desktop.bootstrap import (
    AuthenticatedDesktopIdentity,
    DesktopContext,
    DesktopStartupError,
    build_desktop_context,
)

__all__ = [
    "DesktopSessionManager",
    "load_os_keyring",
    "AuthenticatedDesktopIdentity",
    "DesktopContext",
    "DesktopStartupError",
    "build_desktop_context",
]
