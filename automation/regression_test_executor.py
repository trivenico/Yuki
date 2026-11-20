"""Explicit single-candidate regression execution through the existing approval gate.

This adapter is not a sandbox and never expands a plan into bulk execution.
"""
from __future__ import annotations

from dataclasses import dataclass

from automation.approved_workspace_changes import ApprovedTestRunner, TestRunResult
from automation.authorized_read_tools import ToolAuthorizationError, ToolPrincipal
from automation.regression_test_planner import RegressionTestPlan


@dataclass(frozen=True)
class RegressionTestExecution:
    test_path: str
    result: TestRunResult
    passed: bool
    tests_executed: bool = True
    requires_human_review: bool = True


def run_approved_regression_candidate(
    plan: RegressionTestPlan,
    runner: ApprovedTestRunner,
    *,
    principal: ToolPrincipal,
    test_path: str,
) -> RegressionTestExecution:
    """Run exactly one planned test after the runner's own trusted approval check."""
    if not isinstance(plan, RegressionTestPlan):
        raise TypeError("plan must be a RegressionTestPlan.")
    if type(runner) is not ApprovedTestRunner:
        raise TypeError("runner must be an ApprovedTestRunner.")
    if not isinstance(principal, ToolPrincipal):
        raise TypeError("principal must be a ToolPrincipal.")
    if plan.tests_executed or not plan.requires_human_review:
        raise ToolAuthorizationError("plan state is invalid for supervised execution.")
    if not isinstance(test_path, str) or not test_path.startswith("tests/") or not test_path.endswith(".py"):
        raise ToolAuthorizationError("only a planned Python test under tests/ can be selected.")
    if any(part in {"", ".", ".."} for part in test_path.split("/")) or "\\" in test_path or "\x00" in test_path:
        raise ToolAuthorizationError("test path is not normalized.")
    if test_path not in {candidate.path for candidate in plan.candidates}:
        raise ToolAuthorizationError("test path is not a candidate in this regression plan.")

    module = test_path[:-3].replace("/", ".")
    if module not in runner.allowed_test_modules:
        raise ToolAuthorizationError("test module is not in the runner's explicit allow-list.")

    if runner.isolation_level != "sandbox":
        raise ToolAuthorizationError("host test execution is disabled for regression plans; a verified sandbox runner is required.")

    result = runner.run(principal=principal, test_module=module)
    return RegressionTestExecution(
        test_path=test_path,
        result=result,
        passed=result.exit_code == 0 and not result.timed_out,
    )
