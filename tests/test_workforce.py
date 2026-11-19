import pytest

from core.workforce import AgentProfile, AgentRegistry, default_workforce


def test_default_workforce_contains_100_unique_employees():
    employees = default_workforce()
    assert len(employees) == 100
    assert len({employee.employee_id for employee in employees}) == 100
    assert len({employee.department for employee in employees}) == 10


def test_each_department_has_one_ceo_report_and_nine_team_reports():
    employees = default_workforce()
    for department in {employee.department for employee in employees}:
        team = [employee for employee in employees if employee.department == department]
        assert len(team) == 10
        assert sum(employee.reports_to == "YUKI-CEO" for employee in team) == 1
        lead_id = next(employee.employee_id for employee in team
                       if employee.reports_to == "YUKI-CEO")
        assert all(employee.reports_to == lead_id
                   for employee in team if employee.employee_id != lead_id)


def test_registry_rejects_duplicate_employee_ids():
    employee = default_workforce()[0]
    registry = AgentRegistry([employee])
    with pytest.raises(ValueError, match="duplicate employee_id"):
        registry.register(employee)


def test_recommendation_requires_capability_and_permission_match():
    employee = AgentProfile(
        employee_id="TEST-001",
        name="Approved backend engineer",
        department="Engineering",
        role="Backend Engineer",
        capabilities=frozenset({"python", "api_design"}),
        reports_to="YUKI-CEO",
        permissions=frozenset({"repo:read"}),
    )
    registry = AgentRegistry([employee])
    assert registry.recommend({"python", "api_design"}) == (employee,)
    assert registry.recommend({"python"}, required_permissions={"repo:write"}) == ()
    assert registry.recommend({"python"}, max_autonomy_tier=0) == ()


def test_recommendation_excludes_busy_offline_and_full_capacity_agents():
    base = default_workforce()[0]
    busy = AgentProfile(**{**base.__dict__, "status": "busy"})
    full = AgentProfile(**{**base.__dict__, "employee_id": "TEST-FULL",
                           "active_tasks": 2, "max_concurrent_tasks": 2})
    registry = AgentRegistry([busy, full])
    assert registry.recommend({"operations"}) == ()


def test_invalid_profile_and_query_are_rejected():
    with pytest.raises(ValueError, match="autonomy_tier"):
        AgentProfile(
            employee_id="BAD-001", name="Bad", department="Test", role="Bad",
            capabilities=frozenset({"test"}), reports_to="YUKI-CEO",
            autonomy_tier=9,
        )
    registry = AgentRegistry()
    with pytest.raises(ValueError, match="required_capabilities"):
        registry.recommend(set())



def test_recommendation_rejects_text_unhashable_and_boolean_constraints():
    registry = AgentRegistry()
    for invalid in ("python", [["python"]], [None]):
        with pytest.raises(ValueError):
            registry.recommend(invalid)
    with pytest.raises(ValueError, match="max_autonomy_tier"):
        registry.recommend({"python"}, max_autonomy_tier=True)


def test_profile_rejects_boolean_numeric_counters_and_scores():
    base = default_workforce()[0]
    for field, value in (
        ("active_tasks", True),
        ("max_concurrent_tasks", True),
        ("tasks_completed", True),
        ("quality_score", True),
        ("success_rate", True),
    ):
        with pytest.raises(ValueError):
            AgentProfile(**{**base.__dict__, field: value})
