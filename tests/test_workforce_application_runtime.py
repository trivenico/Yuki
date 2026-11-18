import tempfile
import unittest
from pathlib import Path

from core.agent import YukiCoreAgent
from core.planner import PlanTask, Planner
from core.permissions import PermissionGate
from core.workforce import AgentProfile, AgentRegistry
from core.workforce_application_runtime import build_workforce_application_runtime
from skills.time_skill import TimeSkill


class TimeTaskPlanner(Planner):
    def decompose(self, goal):
        return self.validate_tasks((
            PlanTask("time_check", goal, ("time_read",)),
        ))


class WorkforceApplicationRuntimeTests(unittest.TestCase):
    def _agent(self):
        return YukiCoreAgent(
            skills=[TimeSkill()],
            permission_gate=PermissionGate(approver=lambda request: True),
        )

    def _registry(self):
        return AgentRegistry([AgentProfile(
            employee_id="OPS-TIME-001",
            name="Time Specialist",
            department="Operations",
            role="Time Specialist",
            capabilities=frozenset({"time_read"}),
            reports_to="YUKI-CEO",
            permissions=frozenset(),
            autonomy_tier=1,
        )])

    def test_composes_ceo_worker_and_guarded_skill_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = build_workforce_application_runtime(
                database=Path(directory) / "workforce.sqlite3",
                agent=self._agent(),
                registry=self._registry(),
                planner=TimeTaskPlanner(),
            )
            self.addCleanup(runtime.close)
            execution = runtime.ceo.plan_and_dispatch("Read the current time")
            self.assertEqual(len(execution.assignments), 1)
            result = runtime.run_once()
            self.assertEqual(result["state"], "succeeded")
            report = runtime.ceo.report(execution)
            self.assertTrue(report["complete"])
            self.assertEqual(report["counts"], {"succeeded": 1})

    def test_requires_persistent_database(self):
        with self.assertRaisesRegex(ValueError, "persistent SQLite"):
            build_workforce_application_runtime(
                database=":memory:", agent=self._agent(),
            )

    def test_invalid_agent_fails_before_opening_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "must-not-exist.sqlite3"
            with self.assertRaisesRegex(TypeError, "run_skill"):
                build_workforce_application_runtime(
                    database=path, agent=object(),
                )
            self.assertFalse(path.exists())

    def test_closed_runtime_rejects_worker_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = build_workforce_application_runtime(
                database=Path(directory) / "workforce.sqlite3",
                agent=self._agent(),
                registry=self._registry(),
                planner=TimeTaskPlanner(),
            )
            runtime.close()
            runtime.close()
            self.assertTrue(runtime.closed)
            with self.assertRaisesRegex(RuntimeError, "closed"):
                runtime.run_once()


if __name__ == "__main__":
    unittest.main()
