import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.inference import AuthenticatedInferenceAPI
from core.auth import BuiltInAuthService
from core.device_pairing import DevicePairingService
from core.device_agent import DeviceAgentService
from core.device_resource_reservations import DeviceResourceReservationService
from core.inference_jobs import InferenceJobService


class _Downstream:
    async def __call__(self, scope, receive, send):
        body = b'{"delegated":true}'
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


class AuthenticatedInferenceAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner-one", "a-secure-owner-password")
        self.owner_one = self.auth.authenticate("owner-one", "a-secure-owner-password")
        self.auth.create_account("owner-two", "another-secure-password")
        self.owner_two = self.auth.authenticate("owner-two", "another-secure-password")
        database = root / "inference.sqlite"
        self.pairing = DevicePairingService(database)
        self.reservations = DeviceResourceReservationService(
            root / "reservations.sqlite", self.auth, self.pairing,
        )
        self.jobs = InferenceJobService(
            database, self.pairing, reservation_service=self.reservations,
        )
        self.device_agent = DeviceAgentService(
            root / "device-agent.sqlite", self.pairing,
            reservation_service=self.reservations,
        )
        self.app = AuthenticatedInferenceAPI(
            _Downstream(), self.auth, self.pairing, self.jobs,
            device_agent=self.device_agent,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method, path, payload=None, token=None, extra_headers=None):
        async def run():
            body = json.dumps(payload).encode() if payload is not None else b""
            consumed = False
            sent = []
            async def receive():
                nonlocal consumed
                if consumed:
                    return {"type": "http.disconnect"}
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                sent.append(message)
            headers = []
            if token:
                headers.append((b"authorization", f"Bearer {token}".encode()))
            if extra_headers:
                headers.extend(extra_headers)
            await self.app({
                "type": "http", "method": method, "path": path,
                "headers": headers, "query_string": b"",
            }, receive, send)
            return sent[0]["status"], json.loads(sent[1]["body"])
        return asyncio.run(run())

    def _pair_device(self, session=None):
        status, challenge = self.request(
            "POST", "/v1/devices/pairing-challenges", {}, session or self.owner_one.token
        )
        self.assertEqual(status, 201)
        status, paired = self.request("POST", "/v1/devices/pair", {
            "code": challenge["code"], "name": "local-worker",
        })
        self.assertEqual(status, 201)
        self.reservations.publish_capacity(
            paired["device_token"], memory_bytes=1_000_000,
            cpu_millicores=10_000, gpu_memory_bytes=1_000_000,
        )
        return paired

    def _reserve_job(self, job_id, session=None):
        status, body = self.request(
            "POST", f"/v1/inference/jobs/{job_id}/reservation",
            {
                "memory_bytes": 100, "cpu_millicores": 100,
                "gpu_memory_bytes": 0, "idempotency_key": f"reservation-{job_id}",
                "reservation_secret": f"reservation-secret-for-{job_id}-high-entropy",
            },
            session or self.owner_one.token,
        )
        self.assertEqual(status, 201, body)
        self.assertEqual(body["job_id"], job_id)
        retry_status, retry = self.request(
            "POST", f"/v1/inference/jobs/{job_id}/reservation",
            {
                "memory_bytes": 100, "cpu_millicores": 100,
                "gpu_memory_bytes": 0, "idempotency_key": f"reservation-{job_id}",
                "reservation_secret": f"reservation-secret-for-{job_id}-high-entropy",
            },
            session or self.owner_one.token,
        )
        self.assertEqual(retry_status, 201, retry)
        self.assertEqual(retry["reservation_id"], body["reservation_id"])
        self.assertEqual(retry["fencing_token"], body["fencing_token"])
        return body

    def test_reservation_endpoint_works_without_device_agent(self):
        paired = self._pair_device()
        status, created = self.request("POST", "/v1/inference/jobs", {
            "model_id": "local/model",
            "device_id": paired["device"]["device_id"],
            "input_text": "reservation without device agent",
        }, self.owner_one.token)
        self.assertEqual(status, 201)
        job_id = created["job"]["job_id"]
        self.app = AuthenticatedInferenceAPI(
            _Downstream(), self.auth, self.pairing, self.jobs,
        )
        status, body = self.request(
            "POST", f"/v1/inference/jobs/{job_id}/reservation", {
                "memory_bytes": 100,
                "cpu_millicores": 100,
                "gpu_memory_bytes": 0,
                "idempotency_key": f"reservation-{job_id}",
                "reservation_secret": f"reservation-secret-for-{job_id}-high-entropy",
            }, self.owner_one.token,
        )
        self.assertEqual(status, 201, body)
        self.assertEqual(body["job_id"], job_id)
        self.assertTrue(body["reservation_id"])
        self.assertGreater(body["fencing_token"], 0)

    def test_bound_reservation_survives_journal_acknowledgement_failure(self):
        paired = self._pair_device()
        status, created = self.request("POST", "/v1/inference/jobs", {
            "model_id": "local/model",
            "device_id": paired["device"]["device_id"],
            "input_text": "reserve then recover",
        }, self.owner_one.token)
        self.assertEqual(status, 201)
        job_id = created["job"]["job_id"]
        original_ack = self.jobs.complete_reservation_operation

        def fail_ack(*args, **kwargs):
            raise RuntimeError("simulated acknowledgement outage")

        self.jobs.complete_reservation_operation = fail_ack
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated acknowledgement outage"):
                self.request("POST", f"/v1/inference/jobs/{job_id}/reservation", {
                    "memory_bytes": 100,
                    "cpu_millicores": 100,
                    "gpu_memory_bytes": 0,
                    "idempotency_key": f"reservation-{job_id}",
                    "reservation_secret": f"reservation-secret-for-{job_id}-high-entropy",
                }, self.owner_one.token)
        finally:
            self.jobs.complete_reservation_operation = original_ack

        job = self.jobs.get_job(self.owner_one.user.user_id, job_id)
        self.assertIsNotNone(job.reservation_id)
        ledger = self.reservations.get_reservation_by_id(
            self.owner_one.user.user_id, job.reservation_id,
        )
        self.assertEqual(ledger["status"], "active")
        with self.jobs._connect() as db:
            db.execute(
                "UPDATE inference_reservation_operations SET updated_at='2000-01-01T00:00:00+00:00' WHERE owner_id=? AND job_id=? AND state='pending'",
                (self.owner_one.user.user_id, job_id),
            )
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 1, "abandoned": 0, "deferred": 0},
        )
        self.assertEqual(
            self.jobs.get_job(self.owner_one.user.user_id, job_id).reservation_id,
            job.reservation_id,
        )
        self.assertEqual(
            self.reservations.get_reservation_by_id(
                self.owner_one.user.user_id, job.reservation_id,
            )["status"],
            "active",
        )

    def test_recovery_fails_bound_job_closed_if_reservation_was_released(self):
        paired = self._pair_device()
        status, created = self.request("POST", "/v1/inference/jobs", {
            "model_id": "local/model",
            "device_id": paired["device"]["device_id"],
            "input_text": "bound reservation then released",
        }, self.owner_one.token)
        self.assertEqual(status, 201)
        job_id = created["job"]["job_id"]
        original_ack = self.jobs.complete_reservation_operation
        self.jobs.complete_reservation_operation = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated acknowledgement outage")
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated acknowledgement outage"):
                self.request("POST", f"/v1/inference/jobs/{job_id}/reservation", {
                    "memory_bytes": 100,
                    "cpu_millicores": 100,
                    "gpu_memory_bytes": 0,
                    "idempotency_key": f"reservation-{job_id}",
                    "reservation_secret": f"reservation-secret-for-{job_id}-high-entropy",
                }, self.owner_one.token)
        finally:
            self.jobs.complete_reservation_operation = original_ack

        job = self.jobs.get_job(self.owner_one.user.user_id, job_id)
        self.reservations.release_active_reservation(
            self.owner_one.user.user_id, paired["device"]["device_id"], job_id,
            job.reservation_id, job.reservation_fencing_token, lambda _row: None,
        )
        with self.jobs._connect() as db:
            db.execute(
                "UPDATE inference_reservation_operations SET updated_at='2000-01-01T00:00:00+00:00' WHERE owner_id=? AND job_id=? AND state='pending'",
                (self.owner_one.user.user_id, job_id),
            )
        self.assertEqual(
            self.jobs.reconcile_reservation_operations(stale_after_seconds=1),
            {"completed": 0, "abandoned": 1, "deferred": 0},
        )
        recovered = self.jobs.get_job(self.owner_one.user.user_id, job_id)
        self.assertEqual(recovered.status, "failed")
        self.assertEqual(recovered.error_code, "reservation_invalid")

    def test_owner_authentication_and_one_time_device_pairing(self):
        status, body = self.request("POST", "/v1/devices/pairing-challenges", {})
        self.assertEqual((status, body["error"]), (401, "authentication_required"))
        paired = self._pair_device()
        self.assertTrue(paired["device"]["device_id"].startswith("dev_"))
        self.assertNotIn("owner_id", paired["device"])
        status, body = self.request("POST", "/v1/devices/pair", {
            "code": "x" * 43, "name": "replay",
        })
        self.assertEqual((status, body["error"]), (401, "invalid_pairing_challenge"))

    def test_owner_scoped_job_lifecycle_and_separate_worker_credential(self):
        paired = self._pair_device()
        device_id, device_token = paired["device"]["device_id"], paired["device_token"]
        status, body = self.request("POST", "/v1/inference/jobs", {
            "model_id": "local/model", "device_id": device_id, "input_text": "hello worker",
            "owner_id": self.owner_two.user.user_id,
        }, self.owner_one.token)
        self.assertEqual((status, body["error"]), (400, "invalid_request"))
        status, body = self.request("POST", "/v1/inference/jobs", {
            "model_id": "local/model", "device_id": device_id, "input_text": "hello worker",
        }, self.owner_one.token)
        self.assertEqual(status, 201)
        job_id = body["job"]["job_id"]
        self.assertNotIn("input_text", body["job"])
        self._reserve_job(job_id)
        status, body = self.request("GET", f"/v1/inference/jobs/{job_id}", token=self.owner_two.token)
        self.assertEqual((status, body["error"]), (404, "not_found"))

        status, body = self.request("POST", "/v1/inference/claim", {}, device_token)
        self.assertEqual(status, 200)
        claimed = body["job"]
        self.assertEqual(set(claimed), {"job_id", "model_id", "input_text", "lease_expires_at", "lease_token"})
        self.assertEqual(claimed["input_text"], "hello worker")
        self.assertTrue(claimed["lease_token"])

        status, body = self.request(
            "POST", f"/v1/inference/jobs/{job_id}/complete",
            {"lease_token": claimed["lease_token"], "result_text": "x" * 20_001}, device_token,
        )
        self.assertEqual((status, body["error"]), (413, "result_too_large"))
        status, body = self.request(
            "POST", f"/v1/inference/jobs/{job_id}/complete",
            {"lease_token": claimed["lease_token"], "result_text": "finished"}, device_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["job"]["status"], "succeeded")
        status, body = self.request("GET", f"/v1/inference/jobs/{job_id}", token=self.owner_one.token)
        self.assertEqual((status, body["job"]["result_text"]), (200, "finished"))
        status, body = self.request("GET", "/v1/inference/jobs", token=self.owner_one.token)
        self.assertEqual(status, 200)
        self.assertNotIn("result_text", body["jobs"][0])

    def test_worker_can_renew_live_claim_over_authenticated_api(self):
        paired = self._pair_device()
        device_id, device_token = paired["device"]["device_id"], paired["device_token"]
        status, body = self.request("POST", "/v1/inference/jobs", {
            "model_id": "local/model", "device_id": device_id, "input_text": "renew me",
        }, self.owner_one.token)
        self.assertEqual(status, 201)
        job_id = body["job"]["job_id"]
        self._reserve_job(job_id)
        status, body = self.request("POST", "/v1/inference/claim", {}, device_token)
        self.assertEqual(status, 200)
        claimed = body["job"]

        status, renewed = self.request(
            "POST", f"/v1/inference/worker/jobs/{job_id}/renew",
            {"lease_token": claimed["lease_token"]}, device_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(renewed["job_id"], job_id)
        self.assertGreater(renewed["lease_expires_at"], claimed["lease_expires_at"])

        status, completed = self.request(
            "POST", f"/v1/inference/jobs/{job_id}/complete",
            {"lease_token": claimed["lease_token"], "result_text": "done"}, device_token,
        )
        self.assertEqual((status, completed["job"]["status"]), (200, "succeeded"))

    def test_worker_renewal_rejects_duplicate_authorization_headers(self):
        paired = self._pair_device()
        device_id, device_token = paired["device"]["device_id"], paired["device_token"]
        status, body = self.request("POST", "/v1/inference/jobs", {
            "model_id": "local/model", "device_id": device_id, "input_text": "duplicate auth",
        }, self.owner_one.token)
        self.assertEqual(status, 201)
        job_id = body["job"]["job_id"]
        self._reserve_job(job_id)
        status, body = self.request(
            "POST", f"/v1/inference/worker/jobs/{job_id}/renew",
            {"lease_token": "x" * 43},
            device_token,
            extra_headers=[(b"authorization", f"Bearer {device_token}".encode())],
        )
        self.assertEqual((status, body["error"]), (401, "device_authentication_required"))

    def test_worker_renewal_rejects_revoked_device(self):
        paired = self._pair_device()
        device_id, device_token = paired["device"]["device_id"], paired["device_token"]
        status, body = self.request("POST", "/v1/inference/jobs", {
            "model_id": "local/model", "device_id": device_id, "input_text": "revoke me",
        }, self.owner_one.token)
        self.assertEqual(status, 201)
        job_id = body["job"]["job_id"]
        self._reserve_job(job_id)
        status, body = self.request("POST", "/v1/inference/claim", {}, device_token)
        self.assertEqual(status, 200)
        self.request("DELETE", f"/v1/devices/{device_id}", token=self.owner_one.token)
        status, body = self.request(
            "POST", f"/v1/inference/worker/jobs/{job_id}/renew",
            {"lease_token": body["job"]["lease_token"]}, device_token,
        )
        self.assertEqual((status, body["error"]), (401, "invalid_device_credential"))

    def test_revoked_device_cannot_claim_jobs_and_owner_cannot_revoke_another_device(self):
        paired = self._pair_device()
        device_id, device_token = paired["device"]["device_id"], paired["device_token"]
        status, body = self.request("DELETE", f"/v1/devices/{device_id}", token=self.owner_two.token)
        self.assertEqual((status, body["error"]), (404, "not_found"))
        status, body = self.request("DELETE", f"/v1/devices/{device_id}", token=self.owner_one.token)
        self.assertEqual((status, body["revoked"]), (200, True))
        status, body = self.request("POST", "/v1/inference/worker/jobs/claim", {}, device_token)
        self.assertEqual((status, body["error"]), (401, "invalid_device_credential"))

    def test_device_bearer_cannot_authenticate_owner_endpoints(self):
        paired = self._pair_device()
        status, body = self.request("GET", "/v1/devices", token=paired["device_token"])
        self.assertEqual((status, body["error"]), (401, "authentication_required"))

    def test_unrelated_routes_are_delegated(self):
        status, body = self.request("GET", "/healthz")
        self.assertEqual((status, body["delegated"]), (200, True))


    def test_device_delete_invalidates_reservations_before_revocation(self):
        paired = self._pair_device()
        device_id, device_token = paired["device"]["device_id"], paired["device_token"]
        reservations = DeviceResourceReservationService(
            Path(self.tmp.name) / "reservations.sqlite",
            self.auth, self.pairing,
        )
        reservations.publish_capacity(
            device_token, memory_bytes=4096, cpu_millicores=2000,
            gpu_memory_bytes=0, source_sequence=1,
        )
        reservation = reservations.reserve(
            self.owner_one.token, device_id, "api-revoke-task",
            memory_bytes=1024, cpu_millicores=500, gpu_memory_bytes=0,
            idempotency_key="api-revoke-key",
            reservation_secret="api-revoke-reservation-secret-with-entropy",
        )
        agent = DeviceAgentService(
            Path(self.tmp.name) / "agent.sqlite",
            self.pairing,
            reservation_service=reservations,
        )
        self.app = AuthenticatedInferenceAPI(
            _Downstream(), self.auth, self.pairing, self.jobs, device_agent=agent,
        )

        status, body = self.request(
            "DELETE", f"/v1/devices/{device_id}", token=self.owner_one.token
        )
        self.assertEqual((status, body["revoked"]), (200, True))
        with reservations._db() as db:
            row = db.execute(
                "SELECT status FROM resource_reservations WHERE reservation_id=?",
                (reservation.reservation_id,),
            ).fetchone()
        self.assertEqual(row["status"], "released")
        with self.assertRaises(PermissionError):
            reservations.publish_capacity(
                device_token, memory_bytes=4096, cpu_millicores=2000,
                gpu_memory_bytes=0, source_sequence=2,
            )


if __name__ == "__main__":
    unittest.main()
