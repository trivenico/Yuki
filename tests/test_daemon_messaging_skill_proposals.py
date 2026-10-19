import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from automation.async_scheduler import AsyncJobScheduler, _cron_matches, _next_cron
from automation.daemon import YukiDaemon
from skills.proposals import SkillProposalStore, parse_skill_markdown
from integrations.messaging_bridge import MessagingCommandBridge, SlidingWindowRateLimit, WebhookError


class AsyncSchedulerTests(unittest.TestCase):
    def test_registered_handler_requires_explicit_approval_and_runs_once(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                calls = []
                approvals = []
                scheduler = AsyncJobScheduler(Path(directory) / "jobs.sqlite",
                    approval_required=lambda name, payload: approvals.append((name, payload)) or True)
                scheduler.register_handler("notify", lambda payload: calls.append(payload["message"]))
                job_id = scheduler.add_interval("notify", {"message":"hello"}, interval_seconds=5)
                due = scheduler.list_jobs()[0]["next_run"]
                self.assertEqual(await scheduler.run_once(now=due), 1)
                self.assertEqual(calls, ["hello"])
                self.assertEqual(approvals[0][0], "notify")
                self.assertEqual(scheduler.list_jobs()[0]["last_status"], "succeeded")
                self.assertTrue(scheduler.cancel(job_id))
        asyncio.run(scenario())

    def test_denied_job_does_not_run(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as directory:
                calls = []
                scheduler = AsyncJobScheduler(Path(directory) / "jobs.sqlite", approval_required=lambda *_: False)
                scheduler.register_handler("notify", lambda payload: calls.append(payload))
                scheduler.add_interval("notify", {}, interval_seconds=5)
                due = scheduler.list_jobs()[0]["next_run"]
                self.assertEqual(await scheduler.run_once(now=due), 0)
                self.assertEqual(calls, [])
                self.assertEqual(scheduler.list_jobs()[0]["last_status"], "denied")
        asyncio.run(scenario())

    def test_rejects_unregistered_handlers_and_unsafe_cron(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = AsyncJobScheduler(Path(directory) / "jobs.sqlite", approval_required=lambda *_: True)
            with self.assertRaises(ValueError):
                scheduler.add_interval("shell", {"command":"echo unsafe"}, interval_seconds=5)
            with self.assertRaises(ValueError):
                scheduler.add_cron("shell", {}, expression="* * * *")
            with self.assertRaises(ValueError):
                scheduler.register_handler("shell;rm", lambda _: None)

    def test_cron_matches_utc_and_next_occurrence_is_future(self):
        import datetime
        timestamp = datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
        self.assertTrue(_cron_matches("0 0 * * *", timestamp))
        self.assertGreater(_next_cron("*/15 * * * *", timestamp), timestamp)


class SkillProposalTests(unittest.TestCase):
    DOC = "---\nname: summarize-notes\ndescription: Summarize a provided note\nversion: 1.0.0\n---\n\nSummarize only the supplied text. Do not call tools or access external systems.\n"

    def test_proposal_is_not_activated_until_explicit_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillProposalStore(directory)
            proposal = store.propose(self.DOC)
            self.assertEqual(proposal.name, "summarize-notes")
            self.assertEqual(len(store.list_proposals()), 1)
            with self.assertRaises(PermissionError):
                store.approve("summarize-notes", human_approved=False)
            approved = store.approve("summarize-notes", human_approved=True)
            self.assertTrue(approved.exists())
            self.assertEqual(store.list_proposals(), [])

    def test_rejects_executable_payload_and_unknown_frontmatter(self):
        with self.assertRaises(ValueError):
            parse_skill_markdown(self.DOC + "\nexec('bad')\n")
        with self.assertRaises(ValueError):
            parse_skill_markdown("---\nname: a\ndescription: ok\npermissions: admin\n---\ntext")

class MessagingBridgeTests(unittest.TestCase):
    def test_allowlisted_readonly_and_approval_gated_commands(self):
        dispatched = []
        approvals = []
        bridge = MessagingCommandBridge(
            dispatch=lambda command, argument, context: dispatched.append((command, argument, context)) or "ok",
            approve_action=lambda platform, text: approvals.append((platform, text)) or True,
            allowed_user_ids={"123"})
        raw = json.dumps({"message":{"from":{"id":123},"text":"/status"}}).encode()
        self.assertEqual(bridge.telegram(raw, secret_header="secret", expected_secret="secret"), "ok")
        self.assertEqual(approvals, [])
        raw = json.dumps({"message":{"from":{"id":123},"text":"/task do thing"}}).encode()
        self.assertEqual(bridge.telegram(raw, secret_header="secret", expected_secret="secret"), "ok")
        self.assertEqual(len(approvals), 1)
        with self.assertRaises(PermissionError):
            bridge.telegram(raw, secret_header="wrong", expected_secret="secret")

    def test_user_allowlist_and_rate_limit(self):
        limiter = SlidingWindowRateLimit(limit=1, window_seconds=60)
        self.assertTrue(limiter.allow("user", now=100))
        self.assertFalse(limiter.allow("user", now=101))
        bridge = MessagingCommandBridge(dispatch=lambda *_: "ok", approve_action=lambda *_: True,
                                         allowed_user_ids={"123"})
        raw = json.dumps({"message":{"from":{"id":999},"text":"/status"}}).encode()
        with self.assertRaises(PermissionError):
            bridge.telegram(raw, secret_header="s", expected_secret="s")


if __name__ == "__main__":
    unittest.main()
