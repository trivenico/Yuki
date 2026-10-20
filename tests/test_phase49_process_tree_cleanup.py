"""Phase 49 regression tests for subprocess process-tree cleanup."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from core.subprocess_executor import SubprocessExecutor


@unittest.skipUnless(os.name == "posix", "POSIX process-group semantics required")
class SubprocessProcessTreeTests(unittest.TestCase):
    def test_timeout_terminates_process_group_including_grandchild(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "child.pid"
            child = (
                "import subprocess,sys,time; "
                f"p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                f"open({str(pid_file)!r},'w').write(str(p.pid)); "
                "time.sleep(30)"
            )
            executor = SubprocessExecutor(timeout_seconds=1, max_output_bytes=4096)
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                executor.run([sys.executable, "-c", child], {}, timeout_seconds=1)
            self.assertLess(time.monotonic() - started, 8)
            self.assertTrue(pid_file.exists(), "child should have started before timeout")

            child_pid = int(pid_file.read_text(encoding="utf-8"))
            # A killed descendant may briefly remain as a zombie until the host
            # reaps it. Treat /proc state Z as terminated, not executing.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                proc_stat = Path(f"/proc/{child_pid}/stat")
                if not proc_stat.exists():
                    break
                try:
                    state = proc_stat.read_text(encoding="utf-8").split()[2]
                except (FileNotFoundError, ProcessLookupError):
                    break
                if state == "Z":
                    break
                time.sleep(0.05)
            else:
                self.fail("descendant process remained alive after timeout")

    def test_output_overflow_terminates_process_group(self):
        command = [
            sys.executable, "-c",
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "sys.stdout.write('x'*200000); sys.stdout.flush(); time.sleep(30)",
        ]
        executor = SubprocessExecutor(timeout_seconds=5, max_output_bytes=1024)
        with self.assertRaisesRegex(RuntimeError, "output exceeded"):
            executor.run(command, {})


if __name__ == "__main__":
    unittest.main()
