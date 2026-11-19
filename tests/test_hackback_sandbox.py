import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hackback.sandbox import DockerSandbox, SandboxError, _OUTPUT_TAIL_BYTES


IMAGE = "python:3.11-slim@sha256:" + ("a" * 64)
_REAL_POPEN = subprocess.Popen


class DockerSandboxOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sandbox = DockerSandbox(
            self.tmp.name, allowed_images=[IMAGE], enabled=True,
            approver=lambda _reason: True,
        )

    @patch("hackback.sandbox.subprocess.Popen")
    def test_output_capture_retains_bounded_tail_and_drains_both_pipes(self, popen):
        popen.side_effect = lambda command, **kwargs: _REAL_POPEN(
            [sys.executable, "-c",
             "import sys; sys.stdout.write('A'*500000); "
             "sys.stderr.write('B'*500000)"],
            **kwargs,
        )
        result = self.sandbox.run(IMAGE, ["python", "--version"])
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(len(result["stdout"].encode("utf-8")), _OUTPUT_TAIL_BYTES)
        self.assertEqual(len(result["stderr"].encode("utf-8")), _OUTPUT_TAIL_BYTES)
        self.assertEqual(result["stdout"], "A" * _OUTPUT_TAIL_BYTES)
        self.assertEqual(result["stderr"], "B" * _OUTPUT_TAIL_BYTES)
        self.assertTrue(popen.call_args.kwargs["shell"] is False)
        self.assertIs(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.PIPE)
        mount_index = popen.call_args.args[0].index("--mount")
        self.assertTrue(popen.call_args.args[0][mount_index + 1].endswith(",ro"))
        self.assertNotIn(",rw", popen.call_args.args[0][mount_index + 1])

    @patch("hackback.sandbox.subprocess.Popen")
    def test_timeout_kills_child_and_raises_safe_error(self, popen):
        children = []
        def start_child(_command, **kwargs):
            child = _REAL_POPEN(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                **kwargs,
            )
            children.append(child)
            return child
        popen.side_effect = start_child
        with self.assertRaisesRegex(SandboxError, "timed out"):
            self.sandbox.run(IMAGE, ["python", "--version"], timeout=1)
        self.assertTrue(children[0].poll() is not None)

    @patch("hackback.sandbox.subprocess.Popen", side_effect=OSError("missing"))
    def test_docker_start_failure_is_sanitized(self, _popen):
        with self.assertRaisesRegex(SandboxError, "could not be started"):
            self.sandbox.run(IMAGE, ["python", "--version"])


if __name__ == "__main__":
    unittest.main()
