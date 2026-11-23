from __future__ import annotations

import os

import pytest

from defense.firmware.image_header import (
    FirmwareHeaderInfo,
    inspect_firmware_header,
    load_native_library,
)


def uboot(payload: bytes = b"data") -> bytes:
    header = bytearray(64)
    header[:4] = b"\x27\x05\x19\x56"
    header[12:16] = len(payload).to_bytes(4, "big")
    return bytes(header) + payload


def fit(payload: bytes = b"data") -> bytes:
    total = 40 + len(payload)
    header = bytearray(40)
    header[:4] = b"\xd0\x0d\xfe\xed"
    header[4:8] = total.to_bytes(4, "big")
    header[20:24] = (17).to_bytes(4, "big")
    return bytes(header) + payload


def trx(payload: bytes = b"data") -> bytes:
    total = 28 + len(payload)
    header = bytearray(28)
    header[:4] = b"HDR0"
    header[4:8] = total.to_bytes(4, "little")
    header[12:16] = (2 << 16).to_bytes(4, "little")
    return bytes(header) + payload


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        (uboot(), FirmwareHeaderInfo("uboot", 64, 68, 0)),
        (fit(), FirmwareHeaderInfo("fit", 40, 44, 17)),
        (trx(), FirmwareHeaderInfo("trx", 28, 32, 2)),
    ],
)
def test_supported_headers(image: bytes, expected: FirmwareHeaderInfo) -> None:
    assert inspect_firmware_header(image) == expected


def test_unknown_format_returns_none() -> None:
    assert inspect_firmware_header(b"NOPE" + bytes(32)) is None


@pytest.mark.parametrize(
    "image",
    [
        b"\x27\x05\x19\x56",
        b"\x27\x05\x19\x56" + bytes(59),
        b"\xd0\x0d\xfe\xed" + bytes(36),
        b"HDR0" + bytes(23),
    ],
)
def test_truncated_known_header_rejected(image: bytes) -> None:
    with pytest.raises(ValueError):
        inspect_firmware_header(image)


def test_declared_lengths_cannot_exceed_buffer() -> None:
    bad_uboot = bytearray(64)
    bad_uboot[:4] = b"\x27\x05\x19\x56"
    bad_uboot[12:16] = (100).to_bytes(4, "big")
    bad_fit = bytearray(40)
    bad_fit[:4] = b"\xd0\x0d\xfe\xed"
    bad_fit[4:8] = (100).to_bytes(4, "big")
    bad_trx = bytearray(28)
    bad_trx[:4] = b"HDR0"
    bad_trx[4:8] = (100).to_bytes(4, "little")
    for image in (bad_uboot, bad_fit, bad_trx):
        with pytest.raises(ValueError):
            inspect_firmware_header(image)


def test_rejects_non_bytes_and_non_contiguous_views() -> None:
    with pytest.raises(TypeError):
        inspect_firmware_header("firmware")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        inspect_firmware_header(memoryview(bytearray(16))[::2])


def test_rejects_oversized_buffer() -> None:
    with pytest.raises(ValueError):
        inspect_firmware_header(b"x" * (512 * 1024 * 1024 + 1))


def test_native_c_matches_python_contract_when_configured() -> None:
    library_path = os.environ.get("YUKI_FIRMWARE_HEADER_LIB")
    if not library_path:
        pytest.skip("optional C library not configured")
    load_native_library(library_path)
    for image, expected in (
        (uboot(), FirmwareHeaderInfo("uboot", 64, 68, 0)),
        (fit(), FirmwareHeaderInfo("fit", 40, 44, 17)),
        (trx(), FirmwareHeaderInfo("trx", 28, 32, 2)),
        (b"NOPE" + bytes(32), None),
    ):
        assert inspect_firmware_header(image) == expected
    for image in (
        b"\x27\x05\x19\x56",
        b"\xd0\x0d\xfe\xed" + bytes(36),
        b"HDR0" + bytes(23),
    ):
        with pytest.raises(ValueError):
            inspect_firmware_header(image)
