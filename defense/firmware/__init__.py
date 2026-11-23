"""Read-only firmware container metadata inspection."""

from .image_header import FirmwareHeaderInfo, inspect_firmware_header

__all__ = ["FirmwareHeaderInfo", "inspect_firmware_header"]
