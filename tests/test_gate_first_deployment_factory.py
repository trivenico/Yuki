from types import SimpleNamespace

import pytest

import core.gate_first_deployment_factory as factory


def _dependencies(tmp_path):
    docker = tmp_path / "docker"
    docker.write_text("docker")
    docker.chmod(0o755)
    socket = tmp_path / "docker.sock"
    socket.write_text("socket")
    private = tmp_path / "private"
    private.mkdir()
    private.chmod(0o700)
    callbacks = [lambda *args, **kwargs: True for _ in range(11)]
    return {
        "docker_binary": str(docker),
        "expected_docker_sha256": "a" * 64,
        "docker_socket_path": str(socket),
        "docker_api_version": "v1.41",
        "apparmor_profile": "yuki-worker",
        "private_directory_root": str(private),
        "expected_gate_host_uid": 1000,
        "uid_resolver": callbacks[0],
        "exec_observer": callbacks[1],
        "host_id_provider": callbacks[2],
        "owner_hash_resolver": callbacks[3],
        "evidence_builder": callbacks[4],
        "release_record_publisher": callbacks[5],
        "user_namespace_mapping_verifier": callbacks[6],
        "release_publisher_ownership_verifier": callbacks[7],
        "trusted_observer_configuration_verifier": callbacks[8],
        "host_procfs_visibility_verifier": callbacks[9],
        "host_capabilities_verifier": callbacks[10],
    }


def test_composition_wires_read_only_inspector_collector_and_mandatory_preflight(monkeypatch, tmp_path):
    calls = {}

    class FakeAPI:
        def __init__(self, **kwargs):
            calls["api"] = kwargs

    class FakeDockerClient:
        def __init__(self, **kwargs):
            calls["docker_client"] = kwargs
            self.docker_host = kwargs["docker_host"]

    class FakeCollector:
        def __init__(self, **kwargs):
            calls["collector"] = kwargs

    class FakeExecutor:
        def __init__(self, **kwargs):
            calls["executor"] = kwargs

    def preflight(**kwargs):
        calls["preflight"] = kwargs
        return {"platform": "linux"}

    monkeypatch.setattr(factory, "DockerEngineAPI", FakeAPI)
    monkeypatch.setattr(factory, "DockerGateContainerClient", FakeDockerClient)
    monkeypatch.setattr(factory, "DockerEngineSupervisorCollector", FakeCollector)
    monkeypatch.setattr(factory, "GateFirstDockerSandboxExecutor", FakeExecutor)
    monkeypatch.setattr(factory, "validate_gate_first_host_environment", preflight)

    dependencies = _dependencies(tmp_path)
    executor = factory.build_gate_first_sandbox_executor(**dependencies)

    assert executor is not None
    assert calls["api"]["api_version"] == "v1.41"
    assert calls["docker_client"]["docker_host"] == "unix://" + dependencies["docker_socket_path"]
    assert calls["executor"]["host_preflight"] is not None
    assert calls["executor"]["supervisor_collector_factory"] is not None

    binder = lambda *args: True
    releaser = lambda *args: True
    collector = calls["executor"]["supervisor_collector_factory"](
        gate_identity_binder=binder,
        gate_releaser=releaser,
    )
    assert collector is not None
    assert calls["collector"]["api"] is calls["api"] or isinstance(calls["collector"]["api"], FakeAPI)
    assert calls["collector"]["gate_identity_binder"] is binder
    assert calls["collector"]["gate_releaser"] is releaser
    assert calls["collector"]["release_record_publisher"] is dependencies["release_record_publisher"]

    report = calls["executor"]["host_preflight"]()
    assert report == {"platform": "linux"}
    assert calls["preflight"]["user_namespace_mapping_verifier"] is dependencies["user_namespace_mapping_verifier"]
    assert calls["preflight"]["host_capabilities_verifier"] is dependencies["host_capabilities_verifier"]


def test_composition_rejects_missing_trusted_verifier_before_constructing_docker(monkeypatch, tmp_path):
    constructed = []
    monkeypatch.setattr(factory, "DockerEngineAPI", lambda **kwargs: constructed.append(kwargs))
    dependencies = _dependencies(tmp_path)
    dependencies["host_capabilities_verifier"] = None

    with pytest.raises(TypeError, match="host_capabilities_verifier"):
        factory.build_gate_first_sandbox_executor(**dependencies)

    assert constructed == []


def test_composition_rejects_symlinked_docker_executable(tmp_path):
    target = tmp_path / "docker-real"
    target.write_text("docker")
    target.chmod(0o755)
    link = tmp_path / "docker"
    link.symlink_to(target)
    dependencies = _dependencies(tmp_path)
    dependencies["docker_binary"] = str(link)

    with pytest.raises(ValueError, match="non-symlink"):
        factory.build_gate_first_sandbox_executor(**dependencies)
