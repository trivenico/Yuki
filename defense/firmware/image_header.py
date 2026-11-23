"""Bounded, read-only firmware container header inspection.

Only caller-provided bytes are accepted. This module never opens files, accesses
devices, extracts payloads, or executes firmware. The pure-Python implementation
is authoritative; an explicitly loaded C library is an optional accelerator.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_MAX_IMAGE_BYTES: Final = 512 * 1024 * 1024
_UBOOT_HEADER_SIZE: Final = 64
_FIT_HEADER_SIZE: Final = 40
_TRX_HEADER_SIZE: Final = 28


@dataclass(frozen=True, slots=True)
class FirmwareHeaderInfo:
    format: str
    header_size: int
    total_size: int
    version: int


class _CHeaderInfo(ctypes.Structure):
    _fields_ = [
        ("format", ctypes.c_uint32),
        ("header_size", ctypes.c_uint32),
        ("total_size", ctypes.c_uint64),
        ("version", ctypes.c_uint32),
    ]


_NATIVE_FORMATS = {1: "uboot", 2: "fit", 3: "trx"}
_native_inspector = None


def load_native_library(path: str | Path) -> None:
    """Load an operator-installed C library; loading native code is a trust decision.

    Only load a library built and verified by the deployment. Do not point this
    at an untrusted or user-uploaded shared library.
    """
    global _native_inspector
    library_path = Path(path)
    if not library_path.is_absolute() or library_path.is_symlink():
        raise ValueError("native library path must be absolute and not a symlink")
    if not library_path.is_file():
        raise ValueError("native library path must identify a regular file")
    library = ctypes.CDLL(str(library_path))
    function = library.yuki_fw_inspect
    function.argtypes = [
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_size_t,
        ctypes.POINTER(_CHeaderInfo),
    ]
    function.restype = ctypes.c_int
    _native_inspector = (library, function)


def _python_inspect(data: bytes) -> FirmwareHeaderInfo | None:
    if data[:4] == b"\x27\x05\x19\x56":
        if len(data) < _UBOOT_HEADER_SIZE:
            raise ValueError("truncated U-Boot legacy header")
        payload_size = int.from_bytes(data[12:16], "big")
        total_size = _UBOOT_HEADER_SIZE + payload_size
        if total_size > len(data):
            raise ValueError("U-Boot payload length exceeds supplied buffer")
        return FirmwareHeaderInfo("uboot", _UBOOT_HEADER_SIZE, total_size, 0)

    if data[:4] == b"\xd0\x0d\xfe\xed":
        if len(data) < _FIT_HEADER_SIZE:
            raise ValueError("truncated FIT header")
        total_size = int.from_bytes(data[4:8], "big")
        if total_size < _FIT_HEADER_SIZE or total_size > len(data):
            raise ValueError("invalid FIT total size")
        version = int.from_bytes(data[20:24], "big")
        return FirmwareHeaderInfo("fit", _FIT_HEADER_SIZE, total_size, version)

    if data[:4] == b"HDR0":
        if len(data) < _TRX_HEADER_SIZE:
            raise ValueError("truncated TRX header")
        total_size = int.from_bytes(data[4:8], "little")
        if total_size < _TRX_HEADER_SIZE or total_size > len(data):
            raise ValueError("invalid TRX total size")
        version = int.from_bytes(data[12:16], "little") >> 16
        return FirmwareHeaderInfo("trx", _TRX_HEADER_SIZE, total_size, version)
    return None


def inspect_firmware_header(data: bytes | bytearray | memoryview) -> FirmwareHeaderInfo | None:
    """Identify supported container headers without interpreting payload bytes."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("firmware input must be bytes-like")
    view = memoryview(data)
    if not view.contiguous:
        raise ValueError("firmware input must be contiguous")
    if view.nbytes < 4 or view.nbytes > _MAX_IMAGE_BYTES:
        raise ValueError("firmware input length is outside supported bounds")
    raw = bytes(view)

    if _native_inspector is None:
        return _python_inspect(raw)

    library, function = _native_inspector
    del library  # Keep the CDLL alive for the function pointer's lifetime.
    buffer = (ctypes.c_uint8 * len(raw)).from_buffer_copy(raw)
    result = _CHeaderInfo()
    status = function(buffer, len(raw), ctypes.byref(result))
    if status == 0:
        return None
    if status < 0:
        raise ValueError("malformed firmware header")
    format_name = _NATIVE_FORMATS.get(result.format)
    if format_name is None:
        raise ValueError("native inspector returned an unknown format")
    return FirmwareHeaderInfo(format_name, result.header_size, result.total_size, result.version)
