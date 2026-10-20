"""Offline regression tests for the Phase 8 opt-in framework primitives."""
from __future__ import annotations
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.react_engine import ReActEngine, ToolSpec
from core.daemon import DurableTaskQueue, YukiDaemon, CronScheduler
from core.onboarding import run_onboarding, needs_onboarding
from core.skill_synthesis import SkillProposalStore
from providers.local_backends import create_local_tool_provider

class FakeToolProvider:
    def __init__(self, replies):
        self.replies=list(replies)
        self.seen_messages=[]
    def complete_tool_turn(self, messages, tools):
        self.seen_messages.append([dict(message) for message in messages])
        return self.replies.pop(0)

class ReactTests(unittest.TestCase):
    def test_low_risk_explicit_autonomy_and_observation(self):
        seen=[]
        provider=FakeToolProvider([
            {"type":"tool_calls","calls":[{"id":"c1","name":"lookup","arguments":{"query":"status"}}]},
            {"type":"message","content":"Done"},
        ])
        engine=ReActEngine(provider)
        engine.register(ToolSpec("lookup","Read status",{"type":"object","properties":{"query":{"type":"string"}},"required":["query"],"additionalProperties":False},
                                lambda args: seen.append(args) or {"status":"ok"},risk="low",auto_approve=True))
        result=engine.run("Check status")
        self.assertTrue(result.ok)
        self.assertEqual(result.tool_calls,1)
        self.assertEqual(seen,[{"query":"status"}])
        history=provider.seen_messages[1]
        assistant_index=next(i for i,m in enumerate(history) if m.get("role")=="assistant" and m.get("tool_calls"))
        tool_index=next(i for i,m in enumerate(history) if m.get("role")=="tool")
        self.assertLess(assistant_index,tool_index)
        self.assertEqual(history[assistant_index]["tool_calls"][0]["function"]["name"],"lookup")

    def test_rejects_duplicate_tool_call_ids_before_execution(self):
        seen=[]
        provider=FakeToolProvider([{"type":"tool_calls","calls":[
            {"id":"same","name":"lookup","arguments":{"query":"a"}},
            {"id":"same","name":"lookup","arguments":{"query":"b"}},
        ]}])
        engine=ReActEngine(provider,max_tool_calls=3)
        engine.register(ToolSpec("lookup","Lookup",{"type":"object","properties":{"query":{"type":"string"}},"required":["query"],"additionalProperties":False},
                                lambda args: seen.append(args),risk="low",auto_approve=True))
        result=engine.run("Look up two values")
        self.assertEqual(result.status,"invalid_tool_call")
        self.assertEqual(seen,[])

    def test_rejects_non_finite_json_arguments_before_execution(self):
        seen=[]
        provider=FakeToolProvider([{"type":"tool_calls","calls":[
            {"id":"c1","name":"lookup","arguments":{"query":float("nan")}},
        ]}])
        engine=ReActEngine(provider)
        engine.register(ToolSpec("lookup","Lookup",{"type":"object","properties":{"query":{"type":"number"}},"required":["query"],"additionalProperties":False},
                                lambda args: seen.append(args),risk="low",auto_approve=True))
        result=engine.run("Look up value")
        self.assertEqual(result.status,"invalid_tool_call")
        self.assertEqual(seen,[])

    def test_nested_tool_arguments_are_validated_recursively(self):
        from core.react_engine import _validate_schema
        schema={"type":"object","properties":{"options":{"type":"object","properties":{"count":{"type":"integer","minimum":1,"maximum":3}},"required":["count"],"additionalProperties":False}},"required":["options"],"additionalProperties":False}
        self.assertTrue(_validate_schema(schema,{"options":{"count":2}}))
        self.assertFalse(_validate_schema(schema,{"options":{"count":"2"}}))
        self.assertFalse(_validate_schema(schema,{"options":{"count":4}}))
        self.assertFalse(_validate_schema(schema,{"options":{"count":2,"unsafe":True}}))

    def test_unknown_schema_keywords_fail_closed(self):
        from core.react_engine import _validate_schema
        schema={"type":"object","properties":{"value":{"type":"string","pattern":"^safe$"}}}
        self.assertFalse(_validate_schema(schema,{"value":"safe"}))

    def test_state_changing_tool_denied_without_approver(self):
        provider=FakeToolProvider([
            {"type":"tool_calls","calls":[{"id":"c1","name":"write","arguments":{}}]},
            {"type":"message","content":"No change made"},
        ])
        engine=ReActEngine(provider)
        engine.register(ToolSpec("write","Write file",{"type":"object","properties":{},"additionalProperties":False},
                                lambda _: self.fail("must not execute"),risk="high",auto_approve=True))
        result=engine.run("Write file")
        self.assertTrue(result.ok)
        self.assertEqual(result.tool_calls,1)

    def test_tool_budget_is_enforced(self):
        provider=FakeToolProvider([{"type":"tool_calls","calls":[{"id":"1","name":"noop","arguments":{}},{"id":"2","name":"noop","arguments":{}}]}])
        engine=ReActEngine(provider,max_tool_calls=1)
        engine.register(ToolSpec("noop","No op",{"type":"object","properties":{}},lambda _: {} ,risk="low",auto_approve=True))
        result=engine.run("Do it")
        self.assertEqual(result.status,"tool_budget_exhausted")

class DaemonTests(unittest.TestCase):
    def test_durable_queue_and_registered_handler(self):
        async def scenario(path):
            queue=DurableTaskQueue(path)
            queue.enqueue("sync",{"cursor":2})
            daemon=YukiDaemon(queue)
            seen=[]
            async def sync(payload): seen.append(payload)
            daemon.register("sync",sync)
            self.assertTrue(await daemon.run_once())
            self.assertFalse(await daemon.run_once())
            self.assertEqual(seen,[{"cursor":2}])
        with tempfile.TemporaryDirectory() as d: asyncio.run(scenario(str(Path(d)/"jobs.sqlite")))


    def test_idempotency_and_lease_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            queue=DurableTaskQueue(str(Path(d)/"jobs.sqlite"), lease_seconds=5)
            first=queue.enqueue("sync",{"x":1},idempotency_key="sync:one")
            duplicate=queue.enqueue("sync",{"x":2},idempotency_key="sync:one")
            self.assertEqual(first,duplicate)
            job=queue.claim("worker-a")
            self.assertEqual(job["id"],first)
            self.assertFalse(queue.finish(first,worker_id="worker-b"))
            import sqlite3
            with sqlite3.connect(queue.path) as db:
                db.execute("UPDATE jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?",(first,))
            recovered=queue.claim("worker-b")
            self.assertEqual(recovered["id"],first)
            self.assertEqual(recovered["attempts"],2)
            self.assertTrue(queue.finish(first,worker_id="worker-b"))

    def test_cron_expression_validation_and_enqueue(self):
        with tempfile.TemporaryDirectory() as d:
            queue=DurableTaskQueue(str(Path(d)/"jobs.sqlite"))
            cron=CronScheduler(queue)
            cron.add("10 * * * *","sync",{})
            # Full cron ranges and steps are supported in this phase.
            cron.add("*/5 * * * *","sync",{})
            now=datetime(2026,10,19,12,10,tzinfo=timezone.utc)
            self.assertEqual(cron.tick(now),2)
            self.assertEqual(cron.tick(now),0)

class RuntimeConfigTests(unittest.TestCase):
    def test_runtime_config_path_prefers_onboarding_json_when_present(self):
        import os
        from unittest.mock import patch
        from core.portable_paths import config_path
        with tempfile.TemporaryDirectory() as home:
            onboarding = Path(home) / ".yuki" / "config.json"
            onboarding.parent.mkdir()
            onboarding.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"HOME": home}, clear=False):
                os.environ.pop("YUKI_CONFIG", None)
                self.assertEqual(config_path(), onboarding.resolve())

    def test_runtime_config_path_keeps_yaml_fallback_without_onboarding_file(self):
        import os
        from unittest.mock import patch
        from core.portable_paths import config_path, REPOSITORY_ROOT
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"HOME": home}, clear=False):
                os.environ.pop("YUKI_CONFIG", None)
                self.assertEqual(config_path(), (REPOSITORY_ROOT / "config.yaml").resolve())

    def test_non_mapping_yaml_documents_are_rejected(self):
        from core.config import load_config
        for payload in ("null\n", "false\n", "[]\n", "- provider\n- mock\n"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as d:
                path = Path(d) / "config.yaml"
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    load_config(path)

    def test_empty_yaml_file_still_uses_defaults(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.yaml"
            path.write_text("", encoding="utf-8")
            self.assertEqual(load_config(path)["provider"], "mock")

    def test_unknown_yaml_key_fails_closed(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.yaml"
            path.write_text("provider: mock\nunknown_security_override: true\n",encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_unsupported_runtime_provider_fails_closed(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.yaml"
            path.write_text("provider: arbitrary_cloud\n",encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_yaml_config_rejects_wrong_scalar_types(self):
        from core.config import load_config
        cases = [
            "provider: mock\nvoice_enabled: 'false'\n",
            "provider: mock\nollama_model: 12\n",
            "provider: mock\nname: [unexpected]\n",
        ]
        for payload in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as d:
                path=Path(d)/"config.yaml"
                path.write_text(payload,encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    load_config(path)

    def test_yaml_config_rejects_non_string_keys(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.yaml"
            path.write_text("provider: mock\ntrue: unexpected\n",encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_runtime_config_rejects_non_string_model_settings(self):
        from core.config import load_config
        cases = [
            "provider: mock\nstt_model: [bad]\n",
            "provider: mock\ntts_backend: 12\n",
            "provider: mock\npiper_model_path: false\n",
        ]
        for payload in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as d:
                path=Path(d)/"config.yaml"
                path.write_text(payload,encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    load_config(path)

    def test_runtime_config_rejects_overlong_string_settings(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.yaml"
            path.write_text("provider: mock\nworkspace: '" + ("x" * 2049) + "'\n",encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_runtime_config_rejects_non_string_enumerated_settings(self):
        from core.config import load_config
        cases = [
            "provider: mock\nlanguage_behavior: [auto_detect]\n",
            "provider: mock\nvoice_style: 7\n",
            "provider: mock\nname: ''\n",
        ]
        for payload in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as d:
                path=Path(d)/"config.yaml"
                path.write_text(payload,encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    load_config(path)

    def test_runtime_config_rejects_overlong_allowlist_items(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.yaml"
            path.write_text("provider: mock\nweb_allowed_hosts:\n  - '" + ("x" * 254) + "'\n",encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_runtime_config_rejects_explicit_null_string_fields(self):
        from core.config import load_config
        for payload in (
            "provider: mock\nname: null\n",
            "provider: mock\nollama_model: null\n",
            "provider: mock\nworkspace: null\n",
        ):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as d:
                path=Path(d)/"config.yaml"
                path.write_text(payload,encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    load_config(path)

    def test_runtime_config_rejects_duplicate_allowlist_entries(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.yaml"
            path.write_text("provider: mock\nweb_allowed_hosts:\n  - example.com\n  - ' example.com '\n",encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_duplicate_yaml_keys_are_rejected(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.yaml"
            path.write_text("provider: mock\\nprovider: ollama\\n",encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_config_size_is_checked_on_bytes_read(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.yaml"
            path.write_bytes(b"provider: mock\\n" + b"#" * (128 * 1024))
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_oversized_runtime_config_fails_closed(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.yaml"
            path.write_text("#" + ("x" * (128 * 1024)),encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)


class OnboardingTests(unittest.TestCase):
    def test_first_run_config_never_persists_secret_value(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.json"
            answers=iter(["mock","","","n"])
            cfg=run_onboarding(lambda _:next(answers),path=path)
            self.assertFalse(needs_onboarding(path))
            self.assertEqual(cfg["provider"],"mock")
            self.assertNotIn("api_key",json.loads(path.read_text()))

    def test_existing_config_is_validated_and_never_silently_accepted(self):
        from core.onboarding import run_onboarding
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.json"
            path.write_text(json.dumps({"provider":"mock","unexpected":True}),encoding="utf-8")
            with self.assertRaises(RuntimeError):
                run_onboarding(path=path)
            self.assertIn("unexpected",path.read_text(encoding="utf-8"))

    def test_rejects_secret_values_nested_in_config(self):
        from core.onboarding import save_config
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                save_config({"provider":"mock","security":{"password":"plaintext"}},Path(d)/"config.json")

    def test_rejects_non_loopback_http_and_unsafe_mcp_urls(self):
        from core.onboarding import validate_config
        with self.assertRaises(ValueError):
            validate_config({"provider":"ollama","base_url":"http://192.0.2.1:11434"})
        with self.assertRaises(ValueError):
            validate_config({"provider":"mock","mcp_servers":[{"endpoint":"https://user:pass@example.com/mcp","enabled":False,"allowed_tools":[]}]})

    def test_onboarding_rejects_unknown_nested_keys(self):
        from core.onboarding import validate_config
        invalid = [
            {"provider":"mock","security":{"allow_shell":False,"typo_guard":True}},
            {"provider":"mock","daemon":{"enabled":False,"exec_untrusted":True}},
            {"provider":"mock","messaging":{"enabled":False,"token":"should-not-be-here"}},
        ]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_config(config)

    def test_onboarding_rejects_duplicate_or_blank_mcp_tools_and_endpoints(self):
        from core.onboarding import validate_config
        invalid = [
            {"provider":"mock","mcp_servers":[{"endpoint":"https://mcp.example/sse","enabled":False,"allowed_tools":["read","read"]}]},
            {"provider":"mock","mcp_servers":[{"endpoint":"https://mcp.example/sse","enabled":False,"allowed_tools":[""]}]},
            {"provider":"mock","mcp_servers":[{"endpoint":"https://mcp.example/sse","enabled":False,"allowed_tools":[]},{"endpoint":"https://mcp.example/sse/","enabled":False,"allowed_tools":[]}]},
            {"provider":"mock","mcp_servers":[{"endpoint":"","enabled":False,"allowed_tools":[]}]},
        ]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_config(config)

    def test_onboarding_uses_shared_schema_normalizer(self):
        from core.config_schema import normalize_onboarding_defaults
        from core.onboarding import validate_config
        source = {"provider": "mock", "security": {"allow_network": True}}
        expected = normalize_onboarding_defaults(source)
        actual = validate_config(source)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["version"], 1)
        self.assertEqual(actual["daemon"], {"enabled": False, "poll_seconds": 1.0})
        self.assertEqual(actual["messaging"], {"enabled": False})
    def test_non_string_configuration_keys_are_rejected_cleanly(self):
        from core.onboarding import validate_config
        with self.assertRaisesRegex(ValueError, "keys must be strings"):
            validate_config({"provider": "mock", 7: "unexpected"})
        with self.assertRaisesRegex(ValueError, "security keys must be strings"):
            validate_config({"provider": "mock", "security": {False: True}})
        with self.assertRaisesRegex(ValueError, "MCP server keys must be strings"):
            validate_config({
                "provider": "mock",
                "mcp_servers": [{"endpoint": "https://mcp.example/sse", 1: "unexpected"}],
            })
    def test_api_key_environment_name_uses_portable_ascii_syntax(self):
        from core.onboarding import validate_config
        for name in ("1BAD_KEY", "OPENAI-KEY", "OPENAI.KEY", "ＡＰＩ_KEY", "API KEY"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_config({"provider": "openai", "api_key_env": name})
        self.assertEqual(
            validate_config({"provider": "openai", "api_key_env": "OPENAI_API_KEY"})["api_key_env"],
            "OPENAI_API_KEY",
        )
    def test_cloud_config_requires_secret_environment_reference(self):
        from core.onboarding import validate_config
        with self.assertRaises(ValueError):
            validate_config({"provider":"openai"})
        valid=validate_config({"provider":"openai","api_key_env":"OPENAI_API_KEY"})
        self.assertEqual(valid["api_key_env"],"OPENAI_API_KEY")

    def test_rejects_secret_fields(self):
        from core.onboarding import save_config
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): save_config({"provider":"mock","api_key":"never-save"},Path(d)/"config.json")

    def test_runtime_bridge_requires_unknown_tool_approval_and_version(self):
        from core.config_schema import onboarding_to_runtime_config
        from core.onboarding import validate_config
        config = validate_config({"provider": "ollama"})
        config["security"]["require_approval_for_unknown_tools"] = False
        with self.assertRaisesRegex(ValueError, "requires approval for unknown tools"):
            onboarding_to_runtime_config(config)
        config = validate_config({"provider": "ollama"})
        config["version"] = 2
        with self.assertRaisesRegex(ValueError, "Unsupported onboarding configuration version"):
            onboarding_to_runtime_config(config)
    def test_shared_runtime_bridge_maps_only_supported_safe_settings(self):
        from core.config_schema import onboarding_to_runtime_config
        from core.onboarding import validate_config
        config = validate_config({"provider": "ollama", "model": "qwen2.5"})
        self.assertEqual(onboarding_to_runtime_config(config), {
            "provider": "ollama",
            "ollama_base_url": "http://127.0.0.1:11434",
            "ollama_model": "qwen2.5",
        })

    def test_shared_runtime_bridge_fails_closed_for_unwired_features(self):
        from core.config_schema import onboarding_to_runtime_config
        from core.onboarding import validate_config
        invalid = [
            {"provider": "openai", "api_key_env": "OPENAI_API_KEY"},
            {"provider": "ollama", "security": {"allow_shell": True}},
            {"provider": "ollama", "daemon": {"enabled": True}},
            {"provider": "ollama", "messaging": {"enabled": True}},
            {"provider": "ollama", "mcp_servers": [{"endpoint": "https://mcp.example/sse", "enabled": True, "allowed_tools": ["read"]}]},
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                onboarding_to_runtime_config(validate_config(item))
    def test_runtime_loads_supported_onboarding_json_config(self):
        from core.config import load_config
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({
                "version": 1,
                "provider": "ollama",
                "model": "qwen2.5",
                "base_url": "http://127.0.0.1:11434",
                "api_key_env": None,
                "mcp_servers": [],
                "security": {
                    "allow_network": False,
                    "allow_file_write": False,
                    "allow_shell": False,
                    "require_approval_for_unknown_tools": True,
                },
                "daemon": {"enabled": False, "poll_seconds": 1.0},
                "messaging": {"enabled": False},
            }), encoding="utf-8")
            loaded = load_config(path)
            self.assertEqual(loaded["provider"], "ollama")
            self.assertEqual(loaded["ollama_model"], "qwen2.5")
            self.assertEqual(loaded["ollama_base_url"], "http://127.0.0.1:11434")

    def test_runtime_rejects_onboarding_config_with_unwired_capabilities(self):
        from core.config import load_config
        invalid_configs = [
            {"provider": "openai", "api_key_env": "OPENAI_API_KEY"},
            {"provider": "ollama", "security": {"allow_shell": True}},
            {"provider": "ollama", "daemon": {"enabled": True}},
            {"provider": "ollama", "mcp_servers": [{"endpoint": "https://mcp.example/sse", "enabled": True, "allowed_tools": ["read"]}]},
        ]
        for override in invalid_configs:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as d:
                path = Path(d) / "config.json"
                payload = {
                    "version": 1, "provider": "ollama", "model": "",
                    "base_url": "http://127.0.0.1:11434", "api_key_env": None,
                    "mcp_servers": [],
                    "security": {"allow_network": False, "allow_file_write": False, "allow_shell": False, "require_approval_for_unknown_tools": True},
                    "daemon": {"enabled": False, "poll_seconds": 1.0},
                    "messaging": {"enabled": False},
                }
                payload.update(override)
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    load_config(path)

    def test_normalized_config_is_deeply_isolated_from_input(self):
        from core.onboarding import validate_config
        original = {
            "provider": "mock",
            "mcp_servers": [{"endpoint": "https://mcp.example/sse", "enabled": False, "allowed_tools": ["read"]}],
        }
        normalized = validate_config(original)
        normalized["mcp_servers"][0]["allowed_tools"].append("write")
        self.assertEqual(original["mcp_servers"][0]["allowed_tools"], ["read"])
        original["mcp_servers"][0]["allowed_tools"].append("delete")
        self.assertEqual(normalized["mcp_servers"][0]["allowed_tools"], ["read", "write"])
    def test_partial_sections_receive_safe_defaults(self):
        from core.onboarding import validate_config
        config = validate_config({
            "provider": "mock",
            "security": {"allow_network": True},
            "daemon": {"enabled": True},
        })
        self.assertEqual(config["security"], {
            "allow_network": True,
            "allow_file_write": False,
            "allow_shell": False,
            "require_approval_for_unknown_tools": True,
        })
        self.assertEqual(config["daemon"], {"enabled": True, "poll_seconds": 1.0})
        self.assertEqual(config["messaging"], {"enabled": False})

    def test_normalized_config_does_not_share_mutable_default_sections(self):
        from core.onboarding import validate_config
        first = validate_config({"provider": "mock"})
        second = validate_config({"provider": "mock"})
        first["security"]["allow_shell"] = True
        self.assertFalse(second["security"]["allow_shell"])

    def test_boolean_config_version_is_rejected(self):
        from core.onboarding import validate_config
        with self.assertRaises(ValueError):
            validate_config({"version": True, "provider": "mock"})

    def test_float_config_version_is_rejected(self):
        from core.onboarding import validate_config
        with self.assertRaises(ValueError):
            validate_config({"version": 1.0, "provider": "mock"})

    def test_runtime_json_config_rejects_duplicate_keys(self):
        from core.config import load_config
        payload = '{"version":1,"provider":"mock","provider":"ollama"}'
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(payload, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_onboarding_rejects_duplicate_keys_in_existing_json(self):
        from core.onboarding import run_onboarding
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text('{"version":1,"provider":"mock","provider":"openai"}', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                run_onboarding(path=path)

    def test_shared_strict_json_parser_rejects_nested_duplicate_keys(self):
        from core.config_json import loads_strict
        with self.assertRaises(ValueError):
            loads_strict('{"security":{"allow_shell":false,"allow_shell":true}}')

    def test_runtime_json_config_rejects_nested_duplicate_security_keys(self):
        from core.config import load_config
        payload = (
            '{"version":1,"provider":"ollama","model":"test",'
            '"base_url":"http://127.0.0.1:11434","api_key_env":null,'
            '"mcp_servers":[],"security":{"allow_shell":false,"allow_shell":true},'
            '"daemon":{"enabled":false,"poll_seconds":1.0},"messaging":{"enabled":false}}'
        )
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(payload, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

class SkillSynthesisTests(unittest.TestCase):
    def test_hash_bound_low_risk_review_activates_declarative_file(self):
        with tempfile.TemporaryDirectory() as d:
            store=SkillProposalStore(d)
            proposal=store.propose("summarize-notes",{"name":"Summarize notes","description":"Summarize user-provided notes","risk":"low","capabilities":["text.read"]},"Summarize the supplied text without using external tools.")
            result=store.review("summarize-notes","accept",expected_sha256=proposal["sha256"])
            self.assertEqual(result["status"],"active_declarative")
            self.assertTrue(Path(result["path"]).is_file())

    def test_rejects_hash_mismatch_and_high_risk_promotion(self):
        with tempfile.TemporaryDirectory() as d:
            store=SkillProposalStore(d)
            proposal=store.propose("send-email",{"name":"Send email","description":"Send an email","risk":"high","capabilities":["network.send"]},"Send a message.")
            with self.assertRaises(ValueError): store.review("send-email","accept",expected_sha256="bad")
            with self.assertRaises(PermissionError): store.review("send-email","accept",expected_sha256=proposal["sha256"])

class LocalProviderTests(unittest.TestCase):
    def test_loopback_http_backend_is_constructible(self):
        provider=create_local_tool_provider("llama_cpp","test-model")
        self.assertEqual(provider.base_url,"http://127.0.0.1:8080/v1")

    def test_remote_http_backend_is_rejected(self):
        with self.assertRaises(ValueError): create_local_tool_provider("llama_cpp","test-model",base_url="http://192.0.2.2:8080/v1")

if __name__=="__main__": unittest.main()
