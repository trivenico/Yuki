"""Tests for the one-shot Linux trusted-bootstrap exec barrier."""
from __future__ import annotations

import os
import unittest

from evaluation.linux_exec_barrier import create_linux_exec_barrier


@unittest.skipUnless(
    os.name == "posix" and hasattr(os, "fork"),
    "POSIX fork support is required",
)
class LinuxExecBarrierTests(unittest.TestCase):
    def test_release_requires_readiness_and_is_one_shot(self):
        parent, child = create_linux_exec_barrier()
        try:
            with self.assertRaises(RuntimeError):
                parent.release()
            with self.assertRaises(ValueError):
                parent.wait_ready(0)
            with self.assertRaises(ValueError):
                child.wait_release(float("inf"))
        finally:
            parent.close()
            child.close()

    def test_forked_bootstrap_must_report_ready_before_release(self):
        parent, child = create_linux_exec_barrier()
        pid = os.fork()
        if pid == 0:
            try:
                parent.close()
                child.report_ready()
                child.wait_release(5)
                child.close()
                os._exit(0)
            except BaseException:
                os._exit(71)
        child.close()
        try:
            parent.wait_ready(5)
            parent.release()
            with self.assertRaises(RuntimeError):
                parent.release()
            waited_pid, status = os.waitpid(pid, 0)
            self.assertEqual(waited_pid, pid)
            self.assertTrue(os.WIFEXITED(status))
            self.assertEqual(os.WEXITSTATUS(status), 0)
        finally:
            parent.close()
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass

    def test_child_cannot_wait_before_reporting_ready(self):
        parent, child = create_linux_exec_barrier()
        try:
            with self.assertRaises(RuntimeError):
                child.wait_release(1)
        finally:
            parent.close()
            child.close()


if __name__ == "__main__":
    unittest.main()
