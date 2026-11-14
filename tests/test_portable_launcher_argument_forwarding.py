"""Exercise the generated POSIX launcher as a real subprocess."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.install_portable_host import _write_launcher


@unittest.skipIf(os.name == "nt", "POSIX shell launcher")
class PortableLauncherArgumentForwardingTests(unittest.TestCase):
    def test_executes_quoted_interpreter_and_preserves_opaque_arguments(self):
        with tempfile.TemporaryDirectory(prefix="yuki launcher test ") as temporary:
            root = Path(temporary)
            fake_interpreter = root / "trusted python shim"
            capture = root / "captured arguments.bin"
            fake_interpreter.write_text(
                "#!/bin/sh\nprintf '%s\\0' \"$@\" > \"$YUKI_CAPTURE\"\n",
                encoding="utf-8",
            )
            fake_interpreter.chmod(0o700)

            with patch.object(sys, "executable", str(fake_interpreter)):
                shell_launcher, python_launcher = _write_launcher(
                    root, root / "host policy.json"
                )

            self.assertIsNotNone(python_launcher)
            bundle = root / "USB bundle with spaces"
            opaque_args = [
                "space value",
                "single'quote",
                'double"quote',
                "$HOME; touch SHOULD_NOT_EXIST",
                "unicode-雪-🙂",
                "",
            ]
            environment = os.environ.copy()
            environment["YUKI_CAPTURE"] = str(capture)
            completed = subprocess.run(
                [str(shell_launcher), str(bundle), *opaque_args],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            received = capture.read_bytes().split(b"\0")[:-1]
            self.assertEqual(
                received,
                [
                    str(python_launcher).encode(),
                    str(bundle).encode(),
                    *(item.encode() for item in opaque_args),
                ],
            )
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())


if __name__ == "__main__":
    unittest.main()
