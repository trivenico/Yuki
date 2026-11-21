from __future__ import annotations

import threading

import pytest

from core.device_pairing import DevicePairingError, DevicePairingService, PairedDevice


def _paired_service(tmp_path):
    service = DevicePairingService(tmp_path / "pairing.sqlite3")
    challenge = service.create_pairing_challenge("owner-1")
    credential = service.pair_device(challenge.code, "worker-1")
    return service, credential


def test_authenticated_operation_is_serialized_against_revocation(tmp_path):
    service, credential = _paired_service(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    revoke_started = threading.Event()
    revoked = threading.Event()
    failures = []

    def operation(device):
        assert device.device_id == credential.device.device_id
        entered.set()
        assert release.wait(3)
        return "published"

    def run_worker():
        try:
            service.with_authenticated_device(credential.token, operation)
        except Exception as exc:  # pragma: no cover - diagnostic path
            failures.append(exc)

    worker = threading.Thread(target=run_worker)

    def revoke():
        revoke_started.set()
        try:
            service.revoke_device("owner-1", credential.device.device_id)
            revoked.set()
        except Exception as exc:  # pragma: no cover - diagnostic path
            failures.append(exc)

    worker.start()
    assert entered.wait(3)
    revoker = threading.Thread(target=revoke)
    revoker.start()
    assert revoke_started.wait(3)
    assert not revoked.wait(0.1)
    release.set()
    worker.join(3)
    revoker.join(3)

    assert not worker.is_alive()
    assert not revoker.is_alive()
    assert not failures
    assert revoked.is_set()
    assert service.authenticate_device(credential.token) is None


def test_owner_scoped_operation_rejects_revoked_device(tmp_path):
    service, credential = _paired_service(tmp_path)
    assert service.revoke_device("owner-1", credential.device.device_id)

    with pytest.raises(DevicePairingError):
        service.with_active_device(
            "owner-1", credential.device.device_id, lambda device: device
        )


def test_active_device_ids_callback_runs_with_active_snapshot(tmp_path):
    service, credential = _paired_service(tmp_path)

    result = service.with_active_device_ids(
        "owner-1", lambda device_ids: credential.device.device_id in device_ids
    )

    assert result is True


def test_revocation_callback_failure_rolls_back_pairing_revocation(tmp_path):
    service, credential = _paired_service(tmp_path)

    def fail_invalidation():
        raise RuntimeError("reservation invalidation failed")

    with pytest.raises(RuntimeError, match="reservation invalidation failed"):
        service.revoke_device(
            "owner-1", credential.device.device_id, before_commit=fail_invalidation
        )

    authenticated = service.authenticate_device(credential.token)
    assert isinstance(authenticated, PairedDevice)
    assert authenticated.device_id == credential.device.device_id
