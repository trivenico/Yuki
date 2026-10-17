import json
import sys
import unittest

from core.subprocess_executor import SubprocessExecutor


class SubprocessExecutorTests(unittest.TestCase):
    def test_valid_json_request_response(self):
        executor = SubprocessExecutor(timeout_seconds=3)
        script = "import json,sys; x=json.load(sys.stdin); print(json.dumps({'ok':True,'result':{'value':x['value']+1}}))"
        result = executor.execute([sys.executable, "-c", script], {"value": 4})
        self.assertTrue(result.ok)
        self.assertEqual(result.result, {"value": 5})

    def test_hard_timeout_terminates_wait(self):
        executor = SubprocessExecutor(timeout_seconds=0.2)
        result = executor.execute([sys.executable, "-c", "import time; time.sleep(5)"], {})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "worker_timeout")

    def test_rejects_invalid_argv_and_request(self):
        executor = SubprocessExecutor()
        self.assertEqual(executor.execute([], {}).code, "invalid_argv")
        self.assertEqual(executor.execute([sys.executable, "-c", "pass"], {"n": float("nan")}).code, "invalid_request")

    def test_bounds_request_and_response(self):
        executor = SubprocessExecutor(max_request_bytes=20, max_response_bytes=20)
        self.assertEqual(executor.execute([sys.executable, "-c", "pass"], {"text": "x" * 100}).code, "request_too_large")
        script = "print('x'*100)"
        self.assertEqual(executor.execute([sys.executable, "-c", script], {}).code, "worker_output_too_large")

    def test_rejects_unapproved_environment(self):
        executor = SubprocessExecutor(allowed_env=("SAFE_FLAG",))
        result = executor.execute([sys.executable, "-c", "print('{}')"], {}, env={"API_KEY": "secret"})
        self.assertEqual(result.code, "environment_not_allowed")

    def test_rejects_non_protocol_response(self):
        executor = SubprocessExecutor()
        result = executor.execute([sys.executable, "-c", "print('not json')"], {})
        self.assertEqual(result.code, "invalid_worker_response")


if __name__ == "__main__":
    unittest.main()
