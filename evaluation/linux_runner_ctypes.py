"""ctypes binding for the Linux runner's lifecycle-only C ABI.

This wrapper can spawn and abort the inert helper. It does not provide a
sandbox, isolation controls, or candidate execution.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


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
        self._library = ctypes.CDLL(os.fspath(library_path), use_errno=True)
        self._configure_functions()
        self._handle = self._library.yuki_linux_runner_handle_create()
        if not self._handle:
            error_number = ctypes.get_errno() or 12
            raise NativeRunnerError(error_number, os.strerror(error_number))

    def _configure_functions(self) -> None:
        library = self._library
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
        if type(timeout_ms) is not int or timeout_ms < 0:
            raise ValueError("timeout_ms must be a non-negative integer")
        path = os.fsencode(helper_path)
        if not path.startswith(b"/"):
            raise ValueError("helper path must be absolute")
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
        if type(timeout_ms) is not int or timeout_ms < 0:
            raise ValueError("timeout_ms must be a non-negative integer")
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
        self.close()
