import os
import unittest
from unittest.mock import patch

from automation.specialist_worker_supervisor import SpecialistWorkerSupervisor
from automation.worker_service import load_supervisor_factory, main


class SpecialistWorkerServiceEntryTests(unittest.TestCase):
    def test_factory_spec_requires_module_callable_syntax(self):
        for value in ("", "os.system", "__import__('os'):system", "os:system;exit"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    load_supervisor_factory(value)

    def test_factory_must_resolve_to_callable(self):
        with self.assertRaises(TypeError):
            load_supervisor_factory("automation.specialist_worker_supervisor:missing_factory")

    def test_valid_factory_resolves_without_executing_it(self):
        factory = load_supervisor_factory(
            "automation.specialist_worker_supervisor:SpecialistWorkerSupervisor"
        )
        self.assertIs(factory, SpecialistWorkerSupervisor)

    def test_missing_factory_configuration_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                main()


if __name__ == "__main__":
    unittest.main()
