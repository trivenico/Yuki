import pytest

from automation.specialist_worker_supervisor import SpecialistWorkerSupervisor
from automation.worker_service import build_configured_supervisor, parse_execution_mode


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "direct"),
        ("direct", "direct"),
        (" gate_first ", "gate_first"),
    ],
)
def test_parse_execution_mode(value, expected):
    assert parse_execution_mode(value) == expected


@pytest.mark.parametrize("value", ["", "DIRECT", "fallback", "gate-first", "direct;gate_first"])
def test_parse_execution_mode_rejects_unknown_values(value):
    with pytest.raises(ValueError):
        parse_execution_mode(value)


def test_direct_mode_preserves_operator_factory_result():
    supervisor = object.__new__(SpecialistWorkerSupervisor)
    assert build_configured_supervisor(lambda: supervisor, "direct") is supervisor


def test_gate_first_mode_rejects_wrong_factory_return_before_startup():
    with pytest.raises(TypeError):
        build_configured_supervisor(lambda: object(), "gate_first")


def test_factory_must_be_callable():
    with pytest.raises(TypeError):
        build_configured_supervisor(None, "direct")


def test_unsupported_mode_never_invokes_factory():
    called = []
    with pytest.raises(ValueError):
        build_configured_supervisor(lambda: called.append(True), "fallback")
    assert called == []
