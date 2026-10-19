"""Tests for Deep Research, Multi-Agent Delegation, and Channel Gateway innovations."""

import unittest
from knowledge.deep_research import DeepResearchEngine
from skills.deep_research_skill import DeepResearchSkill
from core.agent_delegator import AgentDelegator
from skills.delegator_skill import DelegatorSkill
from core.channel_gateway import ChannelGateway, ChannelMessage


class AgentInnovationsTests(unittest.TestCase):

    def test_deep_research_engine(self):
        engine = DeepResearchEngine()
        res = engine.research("quantum computing", depth=2)
        self.assertEqual(res["status"], "success")
        self.assertIn("Deep Research Report", res["report"])
        self.assertEqual(res["topic"], "quantum computing")

    def test_deep_research_skill(self):
        skill = DeepResearchSkill()
        res = skill.execute("cybersecurity trends :: 1")
        self.assertTrue(res.ok)
        self.assertIn("Report", res.message)

    def test_agent_delegator(self):
        delegator = AgentDelegator()
        res = delegator.delegate_task("Build security audit report", roles=["researcher", "auditor"])
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["roles_executed"]), 2)
        self.assertIn("Multi-Agent Task Resolution", res["synthesis"])

    def test_delegator_skill(self):
        skill = DelegatorSkill()
        res = skill.execute("Refactor database layer :: coder, auditor")
        self.assertTrue(res.ok)
        self.assertIn("Multi-Agent Task Resolution", res.message)

    def test_channel_gateway(self):
        gateway = ChannelGateway()
        msg = ChannelMessage(channel="slack", user_id="u123", content="Hello Yuki")
        res = gateway.handle_message(msg)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["channel"], "slack")
        self.assertIn("[Yuki Gateway - slack]", res["response"])

        history = gateway.get_session_history("slack:u123")
        self.assertEqual(len(history), 2)


if __name__ == "__main__":
    unittest.main()
