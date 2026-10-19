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

class OnboardingTests(unittest.TestCase):
    def test_first_run_config_never_persists_secret_value(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"config.json"
            answers=iter(["mock","","","n"])
            cfg=run_onboarding(lambda _:next(answers),path=path)
            self.assertFalse(needs_onboarding(path))
            self.assertEqual(cfg["provider"],"mock")
            self.assertNotIn("api_key",json.loads(path.read_text()))

    def test_rejects_secret_fields(self):
        from core.onboarding import save_config
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): save_config({"provider":"mock","api_key":"never-save"},Path(d)/"config.json")

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
