"""Regression tests for strict skill success contracts in the core agent."""
from core.agent import YukiCoreAgent
from core.permissions import PermissionGate
from skills.base import Skill


class ContractSkill(Skill):
    name = "contract_test"
    description = "Test skill result contracts."
    risk = "low"

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def execute(self, argument=""):
        self.calls += 1
        return self.result


def _agent_for(result):
    skill = ContractSkill(result)
    agent = YukiCoreAgent([skill], PermissionGate(lambda _: True))
    return agent, skill


def test_missing_success_flag_is_not_reported_as_success():
    agent, skill = _agent_for({"message": "looks good"})
    result = agent.run_skill("contract_test")
    assert result.ok is False
    assert result.stage == "observe"
    assert result.data == {"error": "invalid_result_contract"}
    assert skill.calls == 1


def test_truthy_string_success_flag_is_not_reported_as_success():
    agent, _ = _agent_for({"ok": "false", "message": "misleading"})
    result = agent.run_skill("contract_test")
    assert result.ok is False
    assert result.data == {"error": "invalid_result_contract"}


def test_false_boolean_is_a_failure_not_success():
    agent, _ = _agent_for({"ok": False, "message": "operation failed"})
    result = agent.run_skill("contract_test")
    assert result.ok is False
    assert result.message == "operation failed"
    assert result.stage == "observe"


def test_true_boolean_is_accepted_as_success():
    agent, _ = _agent_for({"ok": True, "message": "completed", "value": 7})
    result = agent.run_skill("contract_test")
    assert result.ok is True
    assert result.message == "completed"
    assert result.data == {"value": 7}


def test_nul_character_in_skill_argument_is_rejected():
    agent, skill = _agent_for({"ok": True})
    result = agent.run_skill("contract_test", "valid\x00invalid")
    assert result.ok is False
    assert result.stage == "invalid_input"
    assert skill.calls == 0
