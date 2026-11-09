"""Safe opt-in behavior tests for the authenticated real-provider acceptance command."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "real_provider_acceptance.py"
SPEC = importlib.util.spec_from_file_location("yuki_real_provider_acceptance", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _Provider:
    model = "deterministic-test-model"

    def __init__(self, available=True):
        self.available = available
        self.calls = 0

    def is_available(self):
        return self.available

    def generate_chat(self, *, messages, tools, tool_choice="auto"):
        self.calls += 1
        self.messages = list(messages)
        return {"text": MODULE.EXPECTED_RESPONSE, "tool_calls": {"tool_calls": []}}


class _Session:
    token = "test-session-token-that-is-long-enough"


class _Auth:
    def authenticate(self, username, password):
        self.credentials = (username, password)
        return _Session()


class _Runtime:
    def __init__(self, response=MODULE.EXPECTED_RESPONSE, status=200):
        self.app = type("App", (), {"auth": _Auth()})()
        self.response = response
        self.status = status
        self.closed = False
        self.web = object()

    def close(self):
        self.closed = True


class RealProviderAcceptanceTests(unittest.TestCase):
    def test_skips_without_explicit_opt_in_and_never_constructs_provider(self):
        calls = []
        result = MODULE.run_acceptance(
            environ={},
            config_loader=lambda: {},
            provider_factory=lambda config: calls.append(config),
        )
        self.assertEqual(result["result"], "skipped")
        self.assertEqual(calls, [])

    def test_uses_isolated_workspace_and_closes_runtime_after_success(self):
        provider = _Provider()
        observed = {}

        def runtime_factory(config, actual_provider):
            observed["workspace"] = config["workspace"]
            observed["provider"] = actual_provider
            runtime = _Runtime()
            # The request helper is the same production ASGI HTTP boundary;
            # this fake runtime keeps the unit test independent of credentials.
            async def web(scope, receive, send):
                observed["method"] = scope["method"]
                observed["path"] = scope["path"]
                observed["authorization"] = dict(scope["headers"])[b"authorization"]
                await receive()
                body = (b'{"conversation_id":"conversation-acceptance"}'
                        if scope["path"] == "/v1/conversations"
                        else b'{"status":"completed","answer":"Yuki local provider acceptance passed."}')
                await send({"type": "http.response.start", "status": 201 if scope["path"] == "/v1/conversations" else 200})
                await send({"type": "http.response.body", "body": body})
            runtime.web = web
            return runtime

        result = MODULE.run_acceptance(
            environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
            config_loader=lambda: {"provider": "ollama"},
            provider_factory=lambda config: provider,
            runtime_factory=runtime_factory,
        )
        self.assertEqual(result["result"], "passed")
        self.assertEqual(result["transport"], "authenticated_browser_asgi")
        self.assertEqual(observed["provider"], provider)
        self.assertNotEqual(observed["workspace"], "")
        self.assertEqual(observed["method"], "POST")
        self.assertIn("/messages", observed["path"])
        self.assertTrue(observed["authorization"].startswith(b"Bearer "))
        self.assertNotIn("response", result)

    def test_actual_web_composition_root_and_authenticated_route(self):
        provider = _Provider()
        result = MODULE.run_acceptance(
            environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
            config_loader=lambda: {
                "provider": "ollama",
                "storage_backend": "sqlite",
                "ollama_base_url": "http://127.0.0.1:11434",
                "ollama_model": "acceptance-test-model",
                "database_pool_min": 1,
                "database_pool_max": 2,
            },
            provider_factory=lambda config: provider,
        )
        self.assertEqual(result["result"], "passed")
        self.assertEqual(result["transport"], "authenticated_browser_asgi")
        self.assertGreater(provider.calls, 0)
        self.assertTrue(provider.messages)

    def test_unavailable_provider_is_not_used(self):
        provider = _Provider(available=False)
        calls = []
        with self.assertRaisesRegex(RuntimeError, "not available"):
            MODULE.run_acceptance(
                environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
                config_loader=lambda: {"provider": "ollama"},
                provider_factory=lambda config: provider,
                runtime_factory=lambda config, actual: calls.append(actual),
            )
        self.assertEqual(provider.calls, 0)
        self.assertEqual(calls, [])

    def test_unsupported_provider_fails_before_provider_construction(self):
        calls = []
        with self.assertRaisesRegex(RuntimeError, "not supported"):
            MODULE.run_acceptance(
                environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
                config_loader=lambda: {"provider": "mock"},
                provider_factory=lambda config: calls.append(config),
            )
        self.assertEqual(calls, [])

    def test_runtime_failure_and_nonconforming_response_fail_closed(self):
        class FailedRuntime(_Runtime):
            pass

        def failed_factory(config, provider):
            runtime = FailedRuntime()
            async def web(scope, receive, send):
                await receive()
                body = (b'{"conversation_id":"conversation-acceptance"}'
                        if scope["path"] == "/v1/conversations"
                        else b'{"status":"failed","answer":"no"}')
                await send({"type": "http.response.start", "status": 201 if scope["path"] == "/v1/conversations" else 200})
                await send({"type": "http.response.body", "body": body})
            runtime.web = web
            return runtime

        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            MODULE.run_acceptance(
                environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
                config_loader=lambda: {"provider": "ollama"},
                provider_factory=lambda config: _Provider(),
                runtime_factory=failed_factory,
            )

    def test_nonconforming_provider_answer_fails_and_closes_runtime(self):
        runtimes = []

        def factory(config, provider):
            runtime = _Runtime()
            async def web(scope, receive, send):
                await receive()
                if scope["path"] == "/v1/conversations":
                    status, body = 201, b'{"conversation_id":"conversation-acceptance"}'
                else:
                    status = 200
                    body = b'{"status":"completed","answer":"plausible but incorrect"}'
                await send({"type": "http.response.start", "status": status})
                await send({"type": "http.response.body", "body": body})
            runtime.web = web
            runtimes.append(runtime)
            return runtime

        with self.assertRaisesRegex(RuntimeError, "exact-response"):
            MODULE.run_acceptance(
                environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
                config_loader=lambda: {"provider": "ollama"},
                provider_factory=lambda config: _Provider(),
                runtime_factory=factory,
            )
        self.assertTrue(runtimes[0].closed)

    def test_runtime_is_closed_even_when_request_fails(self):
        runtimes = []
        def factory(config, provider):
            runtime = _Runtime()
            async def web(scope, receive, send):
                await receive()
                await send({"type": "http.response.start", "status": 500})
                await send({"type": "http.response.body", "body": b'{}'})
            runtime.web = web
            runtimes.append(runtime)
            return runtime

        with self.assertRaises(RuntimeError):
            MODULE.run_acceptance(
                environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
                config_loader=lambda: {"provider": "ollama"},
                provider_factory=lambda config: _Provider(),
                runtime_factory=factory,
            )
        self.assertTrue(runtimes[0].closed)


if __name__ == "__main__":
    unittest.main()
