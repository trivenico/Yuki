"""ctypes binding for the Linux runner's lifecycle-only C ABI.

This wrapper can spawn and abort the inert helper. It does not provide a
sandbox, isolation controls, or candidate execution.
"""
from __future__ import annotations

import ctypes
import os
import sys


class NativeRunnerError(OSError):
    """Native lifecycle operation failed; errno is preserved."""


class NativeRunnerHandle:
    """Own one opaque native handle until its child is verified reaped.

    The caller must explicitly abort an active child before close(). If abort
    fails, the handle remains open so cleanup can be retried.
    """

    def __init__(self, library_path: str | os.PathLike[str]) -> None:
        if not sys.platform.startswith("linux"):
            raise OSError("native runner ABI is Linux-only")
        self._library = self._load_trusted_library(library_path)
        self._configure_functions()
        version = self._library.yuki_linux_runner_abi_version()
        if version != 1:
            raise OSError(f"unsupported native runner ABI version: {version}")
        self._handle = self._library.yuki_linux_runner_handle_create()
        if not self._handle:
            error_number = ctypes.get_errno() or 12
            raise NativeRunnerError(error_number, os.strerror(error_number))

    @staticmethod
    def _load_trusted_library(library_path: str | os.PathLike[str]):
        """Load one pinned, non-writable ELF shared object or fail closed.

        This checks the selected inode, not its full dependency closure. The
        library must still come from a trusted build with trusted dependencies.
        """
        import stat

        path = os.fsencode(library_path)
        if not path.startswith(b"/"):
            raise ValueError("shared-library path must be absolute")
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_CLOEXEC"):
            raise OSError("required safe native-library open flags are unavailable")
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("native runner library must be a regular file")
            if metadata.st_uid not in (0, os.geteuid()):
                raise PermissionError("native runner library has an untrusted owner")
            if metadata.st_mode & 0o222:
                raise PermissionError("native runner library must not be writable")
            if os.pread(descriptor, 4, 0) != b"\x7fELF":
                raise OSError("native runner library is not an ELF object")
            # /proc/self/fd resolves the already-open inode, avoiding a second
            # pathname lookup between validation and dlopen.
            return ctypes.CDLL(
                f"/proc/self/fd/{descriptor}", use_errno=True
            )
        finally:
            os.close(descriptor)

    def _configure_functions(self) -> None:
        library = self._library
        library.yuki_linux_runner_abi_version.argtypes = []
        library.yuki_linux_runner_abi_version.restype = ctypes.c_uint32
        library.yuki_linux_runner_handle_create.argtypes = []
        library.yuki_linux_runner_handle_create.restype = ctypes.c_void_p

        library.yuki_linux_runner_handle_spawn.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        library.yuki_linux_runner_handle_spawn.restype = ctypes.c_int

        library.yuki_linux_runner_handle_abort.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int),
        ]
        library.yuki_linux_runner_handle_abort.restype = ctypes.c_int

        library.yuki_linux_runner_handle_pid.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
        ]
        library.yuki_linux_runner_handle_pid.restype = ctypes.c_int

        library.yuki_linux_runner_handle_destroy.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
        ]
        library.yuki_linux_runner_handle_destroy.restype = ctypes.c_int

    def _require_open(self) -> int:
        if not self._handle:
            raise RuntimeError("native runner handle is closed")
        return self._handle

    @staticmethod
    def _raise_native(error_number: int, operation: str) -> None:
        code = error_number or 5
        raise NativeRunnerError(code, f"{operation}: {os.strerror(code)}")

    def spawn(self, helper_path: str | os.PathLike[str], nonce: bytes,
              timeout_ms: int = 5000) -> None:
        handle = self._require_open()
        if not isinstance(nonce, bytes) or len(nonce) != 16:
            raise ValueError("nonce must be exactly 16 bytes")
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 2147483647:
            raise ValueError("timeout_ms must be an integer in [0, 2147483647]")
        path = os.fsencode(helper_path)
        if not path.startswith(b"/") or b"\x00" in path:
            raise ValueError("helper path must be absolute and contain no NUL bytes")
        nonce_buffer = (ctypes.c_uint8 * 16).from_buffer_copy(nonce)
        error_number = ctypes.c_int(0)
        result = self._library.yuki_linux_runner_handle_spawn(
            handle, path, nonce_buffer, timeout_ms, ctypes.byref(error_number)
        )
        if result != 0:
            self._raise_native(error_number.value, "native helper spawn")

    @property
    def pid(self) -> int:
        handle = self._require_open()
        pid = ctypes.c_int(-1)
        if self._library.yuki_linux_runner_handle_pid(
            handle, ctypes.byref(pid)
        ) != 0:
            error_number = ctypes.get_errno() or 5
            self._raise_native(error_number, "read native helper PID")
        return pid.value

    def abort(self, timeout_ms: int = 5000) -> None:
        handle = self._require_open()
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 2147483647:
            raise ValueError("timeout_ms must be an integer in [0, 2147483647]")
        error_number = ctypes.c_int(0)
        result = self._library.yuki_linux_runner_handle_abort(
            handle, timeout_ms, ctypes.byref(error_number)
        )
        if result != 0:
            self._raise_native(error_number.value, "native helper abort")

    def close(self) -> None:
        handle = self._require_open()
        error_number = ctypes.c_int(0)
        result = self._library.yuki_linux_runner_handle_destroy(
            handle, ctypes.byref(error_number)
        )
        if result != 0:
            self._raise_native(error_number.value, "destroy native runner handle")
        self._handle = None

    def __enter__(self) -> "NativeRunnerHandle":
        self._require_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        # Never silently abandon a live helper on normal context exit.
        # If abort fails, keep the handle available for caller retry.
        if self.pid > 0:
            self.abort()
        self.close()
