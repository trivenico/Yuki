import unittest

from core.independent_code_review import review_python_changes


class IndependentCodeReviewTests(unittest.TestCase):
    def test_clean_python_change_is_non_blocking(self):
        report = review_python_changes({"src/clean.py": "def add(a, b):\n    return a + b\n"})
        self.assertTrue(report.eligible_for_human_review)
        self.assertEqual(report.blocking_count, 0)
        self.assertEqual(report.files_scanned, 1)

    def test_dynamic_execution_and_shell_true_are_blocking(self):
        source = (
            "import subprocess\n"
            "def run(value):\n"
            "    eval(value)\n"
            "    subprocess.run(value, shell=True)\n"
        )
        report = review_python_changes({"src/unsafe.py": source})
        rules = {item.rule_id for item in report.findings}
        self.assertIn("PY001", rules)
        self.assertIn("PY003", rules)
        self.assertFalse(report.eligible_for_human_review)
        self.assertGreaterEqual(report.blocking_count, 2)

    def test_unsafe_deserialization_and_tls_disable_are_blocking(self):
        source = (
            "import pickle, requests\n"
            "pickle.loads(data)\n"
            "requests.get(url, verify=False)\n"
        )
        report = review_python_changes({"src/network.py": source})
        rules = {item.rule_id for item in report.findings}
        self.assertIn("PY004", rules)
        self.assertIn("PY006", rules)
        self.assertFalse(report.eligible_for_human_review)

    def test_credentials_are_detected_but_values_never_echoed(self):
        secret = "super-secret-value-12345"
        report = review_python_changes({
            "src/config.py": 'eval(user_input); API_KEY = "' + secret + '"\n'
        })
        self.assertFalse(report.eligible_for_human_review)
        self.assertTrue(any(item.rule_id == "SEC001" for item in report.findings))
        rendered = repr(report)
        self.assertNotIn(secret, rendered)
        self.assertIn("redacted", rendered)

    def test_syntax_error_blocks_review(self):
        report = review_python_changes({"src/broken.py": "def broken(:\n pass\n"})
        self.assertFalse(report.eligible_for_human_review)
        self.assertIn("PY000", {item.rule_id for item in report.findings})

    def test_non_python_changes_are_explicitly_incomplete(self):
        report = review_python_changes({"README.md": "# Docs\n"})
        self.assertEqual(report.files_skipped, 1)
        self.assertFalse(report.eligible_for_human_review)

    def test_unsafe_paths_and_oversized_inputs_are_rejected(self):
        for path in ("../outside.py", "/absolute.py", "src\\escape.py"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    review_python_changes({path: "pass\n"})
        with self.assertRaises(ValueError):
            review_python_changes({"large.py": "x" * 250_001})

    def test_review_requires_bounded_nonempty_mapping(self):
        with self.assertRaises(ValueError):
            review_python_changes({})
        with self.assertRaises(ValueError):
            review_python_changes({str(i) + ".py": "pass\n" for i in range(101)})


if __name__ == "__main__":
    unittest.main()
