from __future__ import annotations

import ctypes
import unittest
from unittest.mock import patch

from evaluation import linux_runner_ctypes


class _Function:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class _Library:
    def __init__(self):
        self.yuki_linux_runner_handle_create = _Function(lambda: 1234)
        self.yuki_linux_runner_handle_spawn = _Function(self._spawn)
        self.yuki_linux_runner_handle_abort = _Function(self._abort)
        self.yuki_linux_runner_handle_pid = _Function(self._pid)
        self.yuki_linux_runner_handle_destroy = _Function(self._destroy)
        self.spawn_error = 0
        self.abort_error = 0
        self.destroy_error = 0
        self.current_pid = -1
        self.destroyed = False

    def _spawn(self, handle, path, nonce, timeout, error_out):
        error_out._obj.value = self.spawn_error
        if self.spawn_error == 0:
            self.current_pid = 4321
        return -1 if self.spawn_error else 0

    def _abort(self, handle, timeout, error_out):
        error_out._obj.value = self.abort_error
        if self.abort_error == 0:
            self.current_pid = -1
        return -1 if self.abort_error else 0

    def _pid(self, handle, pid_out):
        pid_out._obj.value = self.current_pid
        return 0

    def _destroy(self, handle, error_out):
        error_out._obj.value = self.destroy_error
        if self.destroy_error == 0:
            self.destroyed = True
        return -1 if self.destroy_error else 0


class NativeRunnerCtypesTests(unittest.TestCase):
    def setUp(self):
        self.library = _Library()
        self.loader = patch.object(
            linux_runner_ctypes.ctypes, "CDLL", return_value=self.library
        )
        self.loader.start()
        self.platform = patch.object(linux_runner_ctypes.sys, "platform", "linux")
        self.platform.start()
        self.addCleanup(self.loader.stop)
        self.addCleanup(self.platform.stop)

    def test_spawn_abort_and_close(self):
        runner = linux_runner_ctypes.NativeRunnerHandle("/tmp/libyuki.so")
        runner.spawn("/opt/yuki/helper", b"0123456789abcdef", 250)
        self.assertEqual(runner.pid, 4321)
        runner.abort(250)
        self.assertEqual(runner.pid, -1)
        runner.close()
        self.assertTrue(self.library.destroyed)

    def test_failed_abort_keeps_handle_available_for_retry(self):
        runner = linux_runner_ctypes.NativeRunnerHandle("/tmp/libyuki.so")
        runner.spawn("/opt/yuki/helper", b"0123456789abcdef")
        self.library.abort_error = 110
        with self.assertRaises(linux_runner_ctypes.NativeRunnerError):
            runner.abort(1)
        self.assertEqual(runner.pid, 4321)
        self.library.abort_error = 0
        runner.abort(100)
        runner.close()

    def test_rejects_invalid_nonce_and_relative_helper_path(self):
        runner = linux_runner_ctypes.NativeRunnerHandle("/tmp/libyuki.so")
        with self.assertRaises(ValueError):
            runner.spawn("/opt/yuki/helper", b"short")
        with self.assertRaises(ValueError):
            runner.spawn("helper", b"0123456789abcdef")
        runner.close()


if __name__ == "__main__":
    unittest.main()
