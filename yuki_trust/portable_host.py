"""Host-owned launch policy for signed Yuki portable bundles.

The policy file belongs to the host installation, never to the removable
bundle. It pins the trust anchor, platform and signed runtime entry paths.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import Any

from yuki_trust.portable_bootstrap import BootstrapError, run_verified_bundle

MAX_POLICY_BYTES = 64 * 1024
_REQUIRED_FIELDS = {
    "schema_version",
    "public_key",
    "expected_platform",
    "python",
    "entrypoint",
    "anti_rollback_state",
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapError("Host launch policy contains duplicate JSON keys.")
        result[key] = value
    return result


def _validate_host_parent_directories(path: Path, *, label: str) -> None:
    """Reject replaceable or symlinked parent directories on POSIX hosts."""
    if not path.is_absolute():
        raise BootstrapError(f"{label} path must be absolute.")
    if os.name == "nt":
        return
    current = path.parent
    while True:
        try:
            info = current.lstat()
        except OSError as exc:
            raise BootstrapError(f"{label} parent directory is unavailable.") from exc
        # macOS exposes /var as a system-managed alias to /private/var.
        # Permit only this exact platform alias; arbitrary symlinked parents
        # remain rejected.
        system_aliases = {
            Path("/var"): Path("/private/var"),
            Path("/tmp"): Path("/private/tmp"),
            Path("/Users"): Path("/System/Volumes/Data/Users"),
        }
        alias_target = system_aliases.get(current) if sys.platform == "darwin" else None
        if alias_target is not None and current.is_symlink() and current.resolve() == alias_target:
            current = current.parent
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise BootstrapError(f"{label} parent directories must be real directories, not symlinks.")
        if info.st_uid not in {0, os.getuid()}:
            raise BootstrapError(f"{label} parent directories must be owned by root or the current user.")
        mode = stat.S_IMODE(info.st_mode)
        # A sticky world-writable directory (for example /tmp) protects
        # entries from deletion by other users; ordinary writable parents do not.
        if mode & 0o022 and not (mode & stat.S_ISVTX):
            raise BootstrapError(f"{label} parent directories must not be group- or world-writable.")
        if current.parent == current:
            break
        current = current.parent


def _read_windows_file_handle_secure(path: Path, *, label: str, max_bytes: int) -> bytes:
    """Read one Windows file through a pinned handle without following a final reparse point.

    FILE_SHARE_READ prevents concurrent write/delete opens while the handle is
    held. Parent path components are still resolved by CreateFileW; this is
    not a handle-relative traversal guarantee for the complete path.
    """
    if max_bytes < 0:
        raise BootstrapError(f"{label} path or size limit is invalid.")
    try:
        import ctypes
        from ctypes import wintypes

        class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime_dwLowDateTime", wintypes.DWORD),
                ("ftCreationTime_dwHighDateTime", wintypes.DWORD),
                ("ftLastAccessTime_dwLowDateTime", wintypes.DWORD),
                ("ftLastAccessTime_dwHighDateTime", wintypes.DWORD),
                ("ftLastWriteTime_dwLowDateTime", wintypes.DWORD),
                ("ftLastWriteTime_dwHighDateTime", wintypes.DWORD),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        get_info = kernel32.GetFileInformationByHandle
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)]
        get_info.restype = wintypes.BOOL
        get_file_type = kernel32.GetFileType
        get_file_type.argtypes = [wintypes.HANDLE]
        get_file_type.restype = wintypes.DWORD
        read_file = kernel32.ReadFile
        read_file.argtypes = [
            wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
        ]
        read_file.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        invalid_handle = wintypes.HANDLE(-1).value
        handle = create_file(
            str(path), 0x80000000, 0x00000001, None, 3,
            0x00200000 | 0x08000000, None,
        )
        if handle == invalid_handle:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed", str(path))
        try:
            info = _BY_HANDLE_FILE_INFORMATION()
            if not get_info(handle, ctypes.byref(info)):
                raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed", str(path))
            attributes = info.dwFileAttributes
            if attributes & 0x00000400:
                raise BootstrapError(f"{label} must not be a reparse point.")
            if attributes & 0x00000010:
                raise BootstrapError(f"{label} must be a regular file, not a directory.")
            # FILE_ATTRIBUTE_DEVICE is not a sufficient discriminator for
            # every Win32 device handle. Require a disk-backed file handle
            # before issuing bounded ReadFile calls.
            if get_file_type(handle) != 0x0001:  # FILE_TYPE_DISK
                raise BootstrapError(f"{label} must be a regular disk file.")
            size = (info.nFileSizeHigh << 32) | info.nFileSizeLow
            if size > max_bytes:
                raise BootstrapError(f"{label} exceeds its size limit.")

            chunks: list[bytes] = []
            total = 0
            while True:
                request = min(1024 * 1024, max_bytes + 1 - total)
                if request <= 0:
                    raise BootstrapError(f"{label} exceeds its size limit.")
                buffer = ctypes.create_string_buffer(request)
                read = wintypes.DWORD()
                if not read_file(handle, buffer, request, ctypes.byref(read), None):
                    raise OSError(ctypes.get_last_error(), "ReadFile failed", str(path))
                if read.value == 0:
                    break
                total += read.value
                if total > max_bytes:
                    raise BootstrapError(f"{label} exceeds its size limit.")
                chunks.append(buffer.raw[:read.value])

            # A pre-existing writer handle may have compatible sharing flags.
            # Re-query the pinned handle and reject any mutation observed
            # during the read, even though this reader itself requested
            # FILE_SHARE_READ only.
            after = _BY_HANDLE_FILE_INFORMATION()
            if not get_info(handle, ctypes.byref(after)):
                raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed", str(path))
            before_identity = (
                info.dwVolumeSerialNumber,
                info.nFileIndexHigh,
                info.nFileIndexLow,
                info.nFileSizeHigh,
                info.nFileSizeLow,
                info.ftLastWriteTime_dwHighDateTime,
                info.ftLastWriteTime_dwLowDateTime,
                info.ftCreationTime_dwHighDateTime,
                info.ftCreationTime_dwLowDateTime,
            )
            after_identity = (
                after.dwVolumeSerialNumber,
                after.nFileIndexHigh,
                after.nFileIndexLow,
                after.nFileSizeHigh,
                after.nFileSizeLow,
                after.ftLastWriteTime_dwHighDateTime,
                after.ftLastWriteTime_dwLowDateTime,
                after.ftCreationTime_dwHighDateTime,
                after.ftCreationTime_dwLowDateTime,
            )
            if before_identity != after_identity or total != size:
                raise BootstrapError(f"{label} changed while being read.")
            return b"".join(chunks)
        finally:
            close_handle(handle)
    except BootstrapError:
        raise
    except OSError as exc:
        # Preserve a useful missing-file diagnostic for callers and policy
        # tests while keeping all other Win32 open/read failures fail-closed.
        if exc.errno == 2 or getattr(exc, "winerror", None) == 2:
            raise BootstrapError(f"{label} is missing.") from exc
        raise BootstrapError(f"{label} could not be securely read.") from exc
    except (AttributeError, TypeError, ValueError) as exc:
        raise BootstrapError(f"{label} could not be securely read.") from exc



def _copy_windows_file_handle_secure(
    source: str | Path,
    target: str | Path,
    *,
    label: str,
    max_bytes: int | None = None,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> None:
    """Copy a regular file through pinned Win32 handles and create the target exclusively.

    The final source component is opened with OPEN_REPARSE_POINT and checked
    through its handle. Target creation uses CREATE_NEW, so an existing file
    or reparse point is never followed or overwritten. Parent components are
    still resolved by Win32 pathname lookup; callers must treat this as
    final-component protection, not full handle-relative traversal.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _INFO(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime_dwLowDateTime", wintypes.DWORD),
                ("ftCreationTime_dwHighDateTime", wintypes.DWORD),
                ("ftLastAccessTime_dwLowDateTime", wintypes.DWORD),
                ("ftLastAccessTime_dwHighDateTime", wintypes.DWORD),
                ("ftLastWriteTime_dwLowDateTime", wintypes.DWORD),
                ("ftLastWriteTime_dwHighDateTime", wintypes.DWORD),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        create = k.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        get_info = k.GetFileInformationByHandle
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_INFO)]
        get_info.restype = wintypes.BOOL
        get_type = k.GetFileType
        get_type.argtypes = [wintypes.HANDLE]
        get_type.restype = wintypes.DWORD
        read = k.ReadFile
        read.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        read.restype = wintypes.BOOL
        write = k.WriteFile
        write.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        write.restype = wintypes.BOOL
        close = k.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL

        invalid = wintypes.HANDLE(-1).value
        src = create(str(source), 0x80000000, 0x00000001, None, 3, 0x00200000 | 0x08000000, None)
        if src == invalid:
            raise OSError(ctypes.get_last_error(), "CreateFileW source failed", str(source))
        dst = None
        try:
            before = _INFO()
            if not get_info(src, ctypes.byref(before)):
                raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle source failed", str(source))
            if before.dwFileAttributes & (0x00000400 | 0x00000010) or get_type(src) != 1:
                raise BootstrapError(f"{label} source must be a regular disk file, not a reparse point or directory.")
            size = (before.nFileSizeHigh << 32) | before.nFileSizeLow
            if (max_bytes is not None and size > max_bytes) or (expected_size is not None and size != expected_size):
                raise BootstrapError(f"{label} source size does not match its permitted bounds.")
            dst = create(str(target), 0x40000000, 0x00000001, None, 1, 0x00200000 | 0x08000000, None)
            if dst == invalid:
                dst = None
                raise OSError(ctypes.get_last_error(), "CreateFileW target failed", str(target))

            digest = hashlib.sha256()
            total = 0
            while True:
                buffer = ctypes.create_string_buffer(1024 * 1024)
                count = wintypes.DWORD()
                if not read(src, buffer, len(buffer), ctypes.byref(count), None):
                    raise OSError(ctypes.get_last_error(), "ReadFile source failed", str(source))
                if count.value == 0:
                    break
                data = buffer.raw[:count.value]
                total += len(data)
                if (max_bytes is not None and total > max_bytes) or (expected_size is not None and total > expected_size):
                    raise BootstrapError(f"{label} source grew beyond its permitted size.")
                digest.update(data)
                offset = 0
                while offset < len(data):
                    written = wintypes.DWORD()
                    chunk = data[offset:]
                    payload = ctypes.create_string_buffer(chunk)
                    if not write(dst, payload, len(chunk), ctypes.byref(written), None) or written.value == 0:
                        raise OSError(ctypes.get_last_error(), "WriteFile target failed", str(target))
                    offset += written.value

            after = _INFO()
            if not get_info(src, ctypes.byref(after)):
                raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle source failed", str(source))
            identity = lambda x: (
                x.dwVolumeSerialNumber, x.nFileIndexHigh, x.nFileIndexLow,
                x.nFileSizeHigh, x.nFileSizeLow,
                x.ftLastWriteTime_dwHighDateTime, x.ftLastWriteTime_dwLowDateTime,
                x.ftCreationTime_dwHighDateTime, x.ftCreationTime_dwLowDateTime,
            )
            if identity(before) != identity(after) or total != size:
                raise BootstrapError(f"{label} source changed while being staged.")
            if expected_size is not None and total != expected_size:
                raise BootstrapError(f"{label} staged size does not match the signed manifest.")
            if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
                raise BootstrapError(f"{label} staged hash does not match the signed manifest.")
        finally:
            if dst is not None:
                close(dst)
            close(src)
    except BootstrapError:
        raise
    except (OSError, AttributeError, TypeError, ValueError) as exc:
        raise BootstrapError(f"{label} could not be securely staged.") from exc



def _inventory_windows_handle_secure(root: Path) -> list[dict[str, Any]]:
    """Inventory a Windows bundle using pinned directory handles and relative opens.

    The root directory itself is opened by path; every descendant is enumerated
    from a pinned directory handle and opened as a single child name relative
    to that handle. FILE_OPEN_REPARSE_POINT is used without FILE_DIRECTORY_FILE:
    the opened object's type is checked through its handle instead.
    """
    import ctypes
    import hashlib
    import struct
    from ctypes import wintypes

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    class _FILE_STANDARD_INFO(ctypes.Structure):
        _fields_ = [
            ("AllocationSize", ctypes.c_longlong),
            ("EndOfFile", ctypes.c_longlong),
            ("NumberOfLinks", wintypes.DWORD),
            ("DeletePending", wintypes.BOOLEAN),
            ("Directory", wintypes.BOOLEAN),
        ]

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime_dwLowDateTime", wintypes.DWORD),
            ("ftCreationTime_dwHighDateTime", wintypes.DWORD),
            ("ftLastAccessTime_dwLowDateTime", wintypes.DWORD),
            ("ftLastAccessTime_dwHighDateTime", wintypes.DWORD),
            ("ftLastWriteTime_dwLowDateTime", wintypes.DWORD),
            ("ftLastWriteTime_dwHighDateTime", wintypes.DWORD),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    get_type = kernel32.GetFileType
    get_type.argtypes = [wintypes.HANDLE]
    get_type.restype = wintypes.DWORD
    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)]
    get_info.restype = wintypes.BOOL
    get_info_ex = kernel32.GetFileInformationByHandleEx
    get_info_ex.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    get_info_ex.restype = wintypes.BOOL
    read_file = kernel32.ReadFile
    read_file.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    read_file.restype = wintypes.BOOL
    nt_create_file = ntdll.NtCreateFile
    nt_create_file.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
        ctypes.POINTER(_OBJECT_ATTRIBUTES), ctypes.POINTER(_IO_STATUS_BLOCK),
        ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
        wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG,
    ]
    nt_create_file.restype = ctypes.c_long

    root_text = str(root)
    if root_text.startswith("\\\\?\\UNC\\"):
        native_root = "\\\\??\\UNC\\" + root_text[8:]
    elif root_text.startswith("\\\\?\\"):
        native_root = "\\\\??\\" + root_text[4:]
    elif root_text.startswith("\\\\"):
        native_root = "\\\\??\\UNC\\" + root_text[2:]
    else:
        native_root = "\\\\??\\" + root_text
    root_name_buffer = ctypes.create_unicode_buffer(native_root)
    root_name_bytes = native_root.encode("utf-16-le")
    if not root_name_bytes or len(root_name_bytes) > 0xFFFC:
        raise BootstrapError("Portable bundle root path is invalid.")
    root_unicode_name = _UNICODE_STRING(
        len(root_name_bytes), len(root_name_bytes),
        ctypes.cast(root_name_buffer, wintypes.LPWSTR),
    )
    root_object_attributes = _OBJECT_ATTRIBUTES(
        ctypes.sizeof(_OBJECT_ATTRIBUTES), None,
        ctypes.pointer(root_unicode_name), 0x00000040, None, None,
    )
    root_io_status = _IO_STATUS_BLOCK()
    root_handle = wintypes.HANDLE()
    root_status = nt_create_file(
        ctypes.byref(root_handle),
        0x00100081,
        ctypes.byref(root_object_attributes),
        ctypes.byref(root_io_status),
        None,
        0x00000080,
        0x00000001,
        0x00000001,
        0x00200000 | 0x00000020,
        None,
        0,
    )
    if root_status < 0:
        raise BootstrapError("Unable to securely open portable bundle root.")
    files: list[dict[str, Any]] = []

    def handle_info(handle: Any) -> tuple[_FILE_ATTRIBUTE_TAG_INFO, _FILE_STANDARD_INFO]:
        attributes = _FILE_ATTRIBUTE_TAG_INFO()
        standard = _FILE_STANDARD_INFO()
        if not get_info_ex(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandleEx(FileAttributeTagInfo) failed")
        if not get_info_ex(handle, 1, ctypes.byref(standard), ctypes.sizeof(standard)):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandleEx(FileStandardInfo) failed")
        if attributes.FileAttributes & 0x00000400:
            raise BootstrapError("Bundle contains a reparse point.")
        return attributes, standard

    def open_child(parent: Any, name: str) -> Any:
        encoded = name.encode("utf-16-le")
        if not encoded or len(encoded) > 0xFFFC or "\x00" in name or "/" in name or "\\" in name:
            raise BootstrapError("Bundle contains an invalid Windows path component.")
        name_buffer = ctypes.create_unicode_buffer(name)
        unicode_name = _UNICODE_STRING(len(encoded), len(encoded), ctypes.cast(name_buffer, wintypes.LPWSTR))
        object_attributes = _OBJECT_ATTRIBUTES(
            ctypes.sizeof(_OBJECT_ATTRIBUTES), parent, ctypes.pointer(unicode_name),
            0x00000040, None, None,
        )
        io_status = _IO_STATUS_BLOCK()
        child = wintypes.HANDLE()
        status = nt_create_file(
            ctypes.byref(child),
            0x00100081,  # SYNCHRONIZE | FILE_READ_ATTRIBUTES | FILE_READ_DATA/LIST_DIRECTORY
            ctypes.byref(object_attributes),
            ctypes.byref(io_status),
            None,
            0x00000080,  # FILE_ATTRIBUTE_NORMAL
            0x00000001,  # FILE_SHARE_READ only
            0x00000001,  # FILE_OPEN
            0x00200000 | 0x00000020,  # FILE_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT
            None,
            0,
        )
        if status < 0:
            raise OSError(status & 0xFFFFFFFF, "NtCreateFile relative child open failed", name)
        return child

    def identity(handle: Any) -> tuple[int, ...]:
        info = _BY_HANDLE_FILE_INFORMATION()
        if not get_info(handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed")
        return (
            info.dwVolumeSerialNumber, info.nFileIndexHigh, info.nFileIndexLow,
            info.nFileSizeHigh, info.nFileSizeLow,
            info.ftLastWriteTime_dwHighDateTime, info.ftLastWriteTime_dwLowDateTime,
            info.ftCreationTime_dwHighDateTime, info.ftCreationTime_dwLowDateTime,
        )

    def visit(directory_handle: Any, prefix: str) -> None:
        # Bound memory while supporting large release directories.
        buffer_size = 16 * 1024 * 1024
        buffer = ctypes.create_string_buffer(buffer_size)
        info_class = 15  # FileFullDirectoryRestartInfo
        while True:
            if not get_info_ex(directory_handle, info_class, buffer, buffer_size):
                error = ctypes.get_last_error()
                if error == 18:  # ERROR_NO_MORE_FILES
                    break
                raise OSError(error, "Directory handle enumeration failed", prefix)
            info_class = 14  # FileFullDirectoryInfo resumes enumeration
            raw = buffer.raw
            offset = 0
            while True:
                if offset + 68 > buffer_size:
                    raise BootstrapError("Windows directory enumeration returned a truncated record.")
                next_offset, _index, _created, _accessed, _written, _changed, end_of_file, _allocation, attrs, name_length, _ea = struct.unpack_from(
                    "<IIqqqqqqIII", raw, offset
                )
                if name_length % 2 or name_length > buffer_size - offset - 68:
                    raise BootstrapError("Windows directory enumeration returned an invalid filename.")
                name = raw[offset + 68:offset + 68 + name_length].decode("utf-16-le", errors="strict")
                if name not in {".", ".."}:
                    relative = f"{prefix}/{name}" if prefix else name
                    if not prefix and name in {"runtime-manifest.json", "runtime-manifest.sig"}:
                        pass
                    else:
                        child = open_child(directory_handle, name)
                        try:
                            tag_info, standard = handle_info(child)
                            if attrs & 0x00000400 or tag_info.FileAttributes & 0x00000400:
                                raise BootstrapError(f"Bundle contains a reparse point: {relative}")
                            is_directory = bool(standard.Directory)
                            if bool(attrs & 0x00000010) != is_directory:
                                raise BootstrapError(f"Bundle entry type changed during inventory: {relative}")
                            if is_directory:
                                visit(child, relative)
                            else:
                                if get_type(child) != 1:  # FILE_TYPE_DISK
                                    raise BootstrapError(f"Bundle contains a non-disk file: {relative}")
                                before = identity(child)
                                digest = hashlib.sha256()
                                size = 0
                                while True:
                                    chunk_buffer = ctypes.create_string_buffer(1024 * 1024)
                                    count = wintypes.DWORD()
                                    if not read_file(child, chunk_buffer, len(chunk_buffer), ctypes.byref(count), None):
                                        raise OSError(ctypes.get_last_error(), "ReadFile during inventory failed", relative)
                                    if count.value == 0:
                                        break
                                    chunk = chunk_buffer.raw[:count.value]
                                    size += len(chunk)
                                    digest.update(chunk)
                                after = identity(child)
                                if before != after or size != standard.EndOfFile:
                                    raise BootstrapError(f"Bundle file changed while being hashed: {relative}")
                                files.append({"path": relative, "size": size, "sha256": digest.hexdigest()})
                        finally:
                            close_handle(child)
                if next_offset == 0:
                    break
                if next_offset < 68 or offset + next_offset >= buffer_size:
                    raise BootstrapError("Windows directory enumeration returned an invalid record offset.")
                offset += next_offset

    try:
        root_attributes, root_standard = handle_info(root_handle)
        if not root_standard.Directory or root_attributes.FileAttributes & 0x00000400:
            raise BootstrapError("Portable bundle root must be a real directory.")
        visit(root_handle, "")
    except BootstrapError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise BootstrapError("Unable to securely inventory portable bundle through directory handles.") from exc
    finally:
        close_handle(root_handle)

    files.sort(key=lambda item: item["path"])
    if not files:
        raise BootstrapError("Runtime bundle contains no regular files.")
    return files


def _read_host_file_secure(path: str | Path, *, label: str, max_bytes: int) -> bytes:
    """Read a host file without following POSIX symlinks during path traversal.

    Directory descriptors pin each traversed directory, so replacing a parent
    pathname after validation cannot redirect the final open to an attacker-
    controlled tree. Windows retains the path-based checks performed by
    _host_file because dir_fd/O_NOFOLLOW semantics are not portable there.
    """
    candidate = Path(path)
    if not candidate.is_absolute() or max_bytes < 0:
        raise BootstrapError(f"{label} path or size limit is invalid.")

    if sys.platform == "darwin":
        # macOS exposes these trusted system aliases as symlinks. Normalize
        # only the documented roots before descriptor-relative traversal.
        # Never apply these aliases on Linux or other POSIX platforms.
        raw = str(candidate)
        for alias, target in (
            ("/var/", "/private/var/"),
            ("/tmp/", "/private/tmp/"),
            ("/Users/", "/System/Volumes/Data/Users/"),
        ):
            if raw.startswith(alias):
                candidate = Path(target + raw[len(alias):])
                break

    if os.name == "nt":
        return _read_windows_file_handle_secure(
            candidate, label=label, max_bytes=max_bytes
        )

    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise BootstrapError(f"{label} requires POSIX O_DIRECTORY and O_NOFOLLOW support.")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    file_flags |= getattr(os, "O_CLOEXEC", 0)
    parts = candidate.parts
    if len(parts) < 2 or parts[0] != "/" or any(part in {"", ".", ".."} for part in parts[1:]):
        raise BootstrapError(f"{label} path must be canonical and absolute.")

    directory_fd = None
    file_fd = None
    try:
        directory_fd = os.open("/", directory_flags)
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            info = os.fstat(next_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(next_fd)
                raise BootstrapError(f"{label} parent is not a real directory.")
            if info.st_uid not in {0, os.getuid()}:
                os.close(next_fd)
                raise BootstrapError(f"{label} parent must be owned by root or the current user.")
            mode = stat.S_IMODE(info.st_mode)
            if mode & 0o022 and not (mode & stat.S_ISVTX):
                os.close(next_fd)
                raise BootstrapError(f"{label} parent must not be group- or world-writable.")
            os.close(directory_fd)
            directory_fd = next_fd

        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise BootstrapError(f"{label} must be a regular file.")
        if info.st_uid not in {0, os.getuid()}:
            raise BootstrapError(f"{label} must be owned by root or the current user.")
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise BootstrapError(f"{label} must not be group- or world-writable.")
        if info.st_size > max_bytes:
            raise BootstrapError(f"{label} exceeds its size limit.")

        chunks = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(file_fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise BootstrapError(f"{label} exceeds its size limit.")
        return data
    except BootstrapError:
        raise
    except OSError as exc:
        raise BootstrapError(f"{label} could not be securely read.") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _host_file(path: str | Path, *, bundle: Path, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise BootstrapError(f"{label} must be a regular, non-symlink host file.")
    # Validate the caller-supplied path before resolving it so symlinked
    # parent directories cannot disappear during canonicalization.
    _validate_host_parent_directories(candidate, label=label)
    resolved = candidate.resolve(strict=True)
    # Also validate the canonical path after resolution.
    _validate_host_parent_directories(resolved, label=label)
    try:
        resolved.relative_to(bundle)
    except ValueError:
        pass
    else:
        raise BootstrapError(f"{label} must be stored outside the removable bundle.")
    if os.name != "nt":
        info = resolved.stat()
        if info.st_uid not in {0, os.getuid()}:
            raise BootstrapError(f"{label} must be owned by the current host user.")
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise BootstrapError(f"{label} must not be group- or world-writable.")
    return resolved


def load_host_policy(policy_path: str | Path, bundle: str | Path) -> dict[str, str]:
    """Load a small, host-owned policy and validate its trust boundary."""
    bundle_candidate = Path(bundle).expanduser()
    if bundle_candidate.is_symlink():
        raise BootstrapError("Portable bundle root must not be a symbolic link.")
    bundle_root = bundle_candidate.resolve(strict=True)
    if not bundle_root.is_dir():
        raise BootstrapError("Portable bundle must be a directory.")

    policy_file = _host_file(policy_path, bundle=bundle_root, label="Host launch policy")
    try:
        policy_bytes = _read_host_file_secure(
            policy_file, label="Host launch policy", max_bytes=MAX_POLICY_BYTES
        )
        value = json.loads(
            policy_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except BootstrapError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("Host launch policy is unreadable or invalid JSON.") from exc

    if not isinstance(value, dict) or set(value) != _REQUIRED_FIELDS:
        raise BootstrapError("Host launch policy has an invalid schema.")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise BootstrapError("Unsupported host launch policy schema version.")

    for field in ("public_key", "expected_platform", "python", "entrypoint", "anti_rollback_state"):
        if not isinstance(value[field], str) or not value[field].strip() or len(value[field]) > 4096:
            raise BootstrapError(f"Host launch policy field {field!r} must be non-empty text.")

    public_key = Path(value["public_key"]).expanduser()
    if not public_key.is_absolute():
        raise BootstrapError("Host trust anchor path must be absolute.")
    _host_file(public_key, bundle=bundle_root, label="Host trust anchor")

    state_path = Path(value["anti_rollback_state"]).expanduser()
    if not state_path.is_absolute():
        raise BootstrapError("Anti-rollback state path must be absolute.")
    _validate_host_parent_directories(state_path, label="Anti-rollback state")
    state_resolved = state_path.parent.resolve(strict=True) / state_path.name
    try:
        state_resolved.relative_to(bundle_root)
    except ValueError:
        pass
    else:
        raise BootstrapError("Anti-rollback state must be stored outside the removable bundle.")
    if state_resolved.is_symlink():
        raise BootstrapError("Anti-rollback state file must not be a symlink.")
    if state_resolved.exists():
        _host_file(state_resolved, bundle=bundle_root, label="Anti-rollback state")

    for field in ("python", "entrypoint"):
        relative = value[field]
        if "\\" in relative:
            raise BootstrapError(f"Host policy {field} path must use canonical bundle-relative separators.")
        parts = relative.split("/")
        if relative.startswith("/") or any(part in {"", ".", ".."} for part in parts):
            raise BootstrapError(f"Host policy {field} path must be canonical and bundle-relative.")

    return {
        "public_key": str(public_key.resolve(strict=True)),
        "expected_platform": value["expected_platform"],
        "python": value["python"],
        "entrypoint": value["entrypoint"],
        "anti_rollback_state": str(state_resolved),
    }


def launch_with_host_policy(
    bundle: str | Path,
    policy_path: str | Path,
    arguments: list[str] | None = None,
) -> int:
    policy = load_host_policy(policy_path, bundle)
    return run_verified_bundle(
        bundle,
        policy["public_key"],
        policy["python"],
        policy["entrypoint"],
        arguments or [],
        expected_platform=policy["expected_platform"],
        anti_rollback_state_path=policy["anti_rollback_state"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Launch a signed Yuki portable bundle using host-installed trust policy."
    )
    parser.add_argument("--bundle", required=True, type=Path, help="Removable bundle directory")
    parser.add_argument("--policy", required=True, type=Path, help="Host-installed launch policy JSON")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="Arguments passed to the signed entrypoint")
    args = parser.parse_args()
    forwarded = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    try:
        return launch_with_host_policy(args.bundle, args.policy, forwarded)
    except (OSError, ValueError, BootstrapError) as exc:
        print(f"Yuki host launcher refused to launch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
