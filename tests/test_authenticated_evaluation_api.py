import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from api.evaluations import AuthenticatedEvaluationAPI
from evaluation.benchmark_registry import BenchmarkDefinition, BenchmarkRegistry
from evaluation.model_eval import EvalCase
from evaluation.run_store import EvaluationRunStore
from evaluation.service import EvaluationService, TrustedEvaluationAdapter


class _App:
    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


class _Auth:
    def __init__(self, owner):
        self.owner = owner

    def resolve_session(self, token):
        return SimpleNamespace(user_id=self.owner) if token == "valid-token" else None


class AuthenticatedEvaluationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        registry = BenchmarkRegistry()
        benchmark = registry.register(BenchmarkDefinition(
            "api-smoke", 1, "API smoke",
            (EvalCase("case-1", "reasoning", "Explain retries", required_terms=("retry",)),),
        ))
        self.benchmark = benchmark
        store = EvaluationRunStore(Path(self.temp.name) / "runs.sqlite3")
        adapter = TrustedEvaluationAdapter(
            "provider-internal", "model-internal",
            lambda prompt, *, timeout_seconds: "A retry can be bounded.",
        )
        service = EvaluationService(
            registry, store, lambda owner, candidate: adapter,
        )
        self.store = store
        self.api = AuthenticatedEvaluationAPI(_App(), _Auth("owner-a"), service)

    def tearDown(self):
        self.temp.cleanup()

    def _request(self, method, path, payload=None, owner_token="valid-token", query=b""):
        messages = [{
            "type": "http.request",
            "body": json.dumps(payload or {}).encode("utf-8"),
            "more_body": False,
        }]
        async def receive():
            return messages.pop(0)
        sent = []
        async def send(message):
            sent.append(message)
        scope = {
            "type": "http", "method": method, "path": path,
            "query_string": query,
            "headers": (([(b"authorization", f"Bearer {owner_token}".encode())]
                         if owner_token else [])
                        + [(b"idempotency-key", b"api-request-1")]),
        }
        asyncio.run(self.api(scope, receive, send))
        status = sent[0]["status"]
        body = json.loads(sent[1].get("body", b"{}") or b"{}")
        return status, body

    def test_post_runs_only_server_configured_adapter_and_get_is_owner_scoped(self):
        payload = {
            "benchmark_id": "api-smoke",
            "benchmark_version": 1,
            "benchmark_digest": self.benchmark.digest,
            "candidate_id": "candidate-a",
        }
        status, body = self._request(
            "POST", "/v1/evaluations/runs", payload,
        )
        self.assertEqual(status, 201)
        run = body["run"]
        self.assertEqual(run["provider_id"], "provider-internal")
        self.assertEqual(run["model_id"], "model-internal")
        self.assertNotIn("owner_id", run)
        self.assertNotIn("idempotency_key", run)
        status, listed = self._request("GET", "/v1/evaluations/runs")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["runs"]), 1)
        replay_status, replay_body = self._request("POST", "/v1/evaluations/runs", payload)
        self.assertEqual(replay_status, 201)
        self.assertEqual(replay_body["run"]["run_id"], run["run_id"])
        self.api.auth.owner = "owner-b"
        other_owner_status, _ = self._request(
            "GET", "/v1/evaluations/runs/" + run["run_id"],
        )
        self.assertEqual(other_owner_status, 404)
        self.api.auth.owner = "owner-a"
        status, missing = self._request(
            "GET", "/v1/evaluations/runs/" + run["run_id"], owner_token="invalid-token",
        )
        self.assertEqual(status, 401)

    def test_request_cannot_submit_report_or_provider_identity(self):
        payload = {
            "benchmark_id": "api-smoke", "benchmark_version": 1,
            "benchmark_digest": self.benchmark.digest, "candidate_id": "candidate-a",
            "report": {"promotion_eligible": True}, "provider_id": "forged",
        }
        status, body = self._request("POST", "/v1/evaluations/runs", payload)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_request")

    def test_idempotency_header_is_required(self):
        payload = {
            "benchmark_id": "api-smoke", "benchmark_version": 1,
            "benchmark_digest": self.benchmark.digest, "candidate_id": "candidate-a",
        }
        # Build a request without the idempotency header.
        messages = [{"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}]
        async def receive(): return messages.pop(0)
        sent = []
        async def send(message): sent.append(message)
        scope = {"type": "http", "method": "POST", "path": "/v1/evaluations/runs",
                 "headers": [(b"authorization", b"Bearer valid-token")], "query_string": b""}
        asyncio.run(self.api(scope, receive, send))
        self.assertEqual(sent[0]["status"], 400)


    def test_active_idempotency_claim_returns_conflict(self):
        payload = {
            "benchmark_id": "api-smoke", "benchmark_version": 1,
            "benchmark_digest": self.benchmark.digest, "candidate_id": "candidate-a",
        }
        request_digest = self.store.request_digest_for(
            benchmark_id="api-smoke", benchmark_version=1,
            benchmark_digest=self.benchmark.digest, candidate_id="candidate-a",
            provider_id="provider-internal", model_id="model-internal",
            environment={},
        )
        token = self.store.acquire_claim(
            owner_id="owner-a", idempotency_key="api-request-1",
            request_digest=request_digest,
        )
        try:
            status, body = self._request("POST", "/v1/evaluations/runs", payload)
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "evaluation_in_progress")
        finally:
            self.store.release_claim(
                owner_id="owner-a", idempotency_key="api-request-1",
                lease_token=token,
            )



if __name__ == "__main__":
    unittest.main()
