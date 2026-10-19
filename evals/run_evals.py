"""Run Yuki's deterministic safety evaluations without external services."""
from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="Run the pull-request subset.")
    args = parser.parse_args(argv)

    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT), pattern="test_*.py", top_level_dir=str(ROOT.parent))
    tests = list(_flatten(suite))
    if args.fast:
        fast_names = {
            "test_case_catalog_is_valid",
            "test_mock_provider_contract",
            "test_ollama_malformed_json_fails_closed",
            "test_ollama_oversized_response_fails_closed",
            "test_ollama_timeout_is_bounded",
            "test_unregistered_destructive_skill_is_blocked",
            "test_oversized_model_output_is_rejected",
            "test_denied_approval_stops_without_execution",
            "test_chat_output_is_never_dispatched_as_a_tool",
        }
        tests = [test for test in tests if test._testMethodName in fast_names]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(tests))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
