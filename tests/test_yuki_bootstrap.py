import unittest

from api.bootstrap import _prompt_from_messages
from api.server import _loopback_host
from api.bootstrap import _authorize_companion_memory


class YukiBootstrapTests(unittest.TestCase):
    def test_prompt_builder_preserves_role_boundaries(self):
        prompt = _prompt_from_messages([
            {"role": "system", "content": "You are Yuki."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ])
        self.assertIn("<|system|>\nYou are Yuki.", prompt)
        self.assertIn("<|user|>\nHello", prompt)
        self.assertIn("<|assistant|>\nHi", prompt)

    def test_prompt_builder_rejects_invalid_role_and_shape(self):
        with self.assertRaisesRegex(ValueError, "role or content"):
            _prompt_from_messages([{"role": "tool", "content": "untrusted"}])
        with self.assertRaisesRegex(ValueError, "only role and content"):
            _prompt_from_messages([{"role": "user", "content": "hello", "trusted": True}])

    def test_prompt_builder_enforces_transcript_limit(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            _prompt_from_messages([{"role": "user", "content": "x" * 100_001}])

    def test_companion_memory_policy_is_owner_scoped_and_restricted(self):
        actor = "owner-" + "a" * 64
        self.assertTrue(_authorize_companion_memory(actor, "agent_memory:read", "assistant"))
        self.assertTrue(_authorize_companion_memory(actor, "agent_memory:write", "assistant"))
        self.assertFalse(_authorize_companion_memory(actor, "agent_memory:read", "agent_memory"))
        self.assertTrue(_authorize_companion_memory(actor, "owner_profile:read", "owner_profile"))
        self.assertTrue(_authorize_companion_memory(actor, "owner_profile:write", "owner_profile"))
        self.assertFalse(_authorize_companion_memory(actor, "agent_memory:share", "assistant"))
        self.assertFalse(_authorize_companion_memory(actor, "agent_memory:prune", "assistant"))
        self.assertFalse(_authorize_companion_memory(actor, "owner_profile:share", "owner_profile"))
        self.assertFalse(_authorize_companion_memory(actor, "owner_profile:write", "assistant"))
        self.assertFalse(_authorize_companion_memory(actor, "agent_memory:write", "owner_profile"))
        self.assertFalse(_authorize_companion_memory(actor, "agent_memory:read", "research"))
        self.assertFalse(_authorize_companion_memory("owner-untrusted", "agent_memory:read", "assistant"))

    def test_server_accepts_ipv4_and_ipv6_loopback(self):
        self.assertEqual(_loopback_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(_loopback_host("::1"), "::1")

    def test_server_rejects_non_loopback_bind_address(self):
        with self.assertRaisesRegex(Exception, "loopback"):
            _loopback_host("0.0.0.0")


if __name__ == "__main__":
    unittest.main()
