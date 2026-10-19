import json
import sys
import unittest

from core.subprocess_executor import SubprocessExecutor


class SubprocessExecutorTests(unittest.TestCase):
    def test_json_round_trip(self):
        executor = SubprocessExecutor(timeout_seconds=3, max_output_bytes=4096)
        script = "import json,sys; x=json.load(sys.stdin); print(json.dumps({'ok':True,'result':x}))"
        result = executor.execute([sys.executable, "-c", script], {"hello": "yuki"})
        self.assertEqual(result, {"ok": True, "result": {"hello": "yuki"}})

    def test_rejects_output_over_limit(self):
        executor = SubprocessExecutor(timeout_seconds=3, max_output_bytes=256)
        script = "import sys; sys.stdout.write('x'*1000000); sys.stdout.flush()"
        with self.assertRaisesRegex(ValueError, "output limit"):
            executor.execute([sys.executable, "-c", script], {})

    def test_kills_timed_out_process(self):
        executor = SubprocessExecutor(timeout_seconds=0.2, max_output_bytes=4096)
        script = "import time; time.sleep(10)"
        with self.assertRaisesRegex(TimeoutError, "timed out"):
            executor.execute([sys.executable, "-c", script], {}, timeout_seconds=0.2)

    def test_rejects_oversized_input_before_spawn(self):
        executor = SubprocessExecutor(timeout_seconds=1, max_input_bytes=128)
        with self.assertRaisesRegex(ValueError, "input limit"):
            executor.execute([sys.executable, "-c", "raise SystemExit(0)"], {"payload": "x" * 200})

    def test_rejects_shell_string(self):
        executor = SubprocessExecutor()
        with self.assertRaises(ValueError):
            executor.execute("echo hello", {})


if __name__ == "__main__":
    unittest.main()
