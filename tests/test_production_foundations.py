import tempfile
import unittest
from pathlib import Path

from core.agent_identity import generate_identity, sign_payload, verify_envelope, IdentityUnavailable
from core.continuous_eval import EvalCase, run_dataset
from core.governance import Governance
from core.output_guard import OutputGuard
from companion.prompt_manager import PromptManager
from knowledge.curator import KnowledgeCurator
from voice.voice_production import VoiceProduction


class ProductionFoundationTests(unittest.TestCase):
    def test_prompt_profile_loads(self):
        profile = PromptManager().load()
        self.assertEqual(profile.name, "Yuki")
        self.assertTrue(profile.system_guidance)

    def test_output_guard_blocks_private_key(self):
        result = OutputGuard().check("-----BEGIN PRIVATE KEY-----secret")
        self.assertFalse(result.allowed)

    def test_governance_fails_closed(self):
        decision = Governance().request(action_id="1", action="delete", actor="test", reason="test")
        self.assertFalse(decision.approved)

    def test_eval_runner_checks_text(self):
        report = run_dataset([EvalCase("x", "test", "input", ("ok",))], lambda _: "OK")
        self.assertEqual(report["passed"], 1)

    def test_voice_requires_explicit_consent(self):
        with self.assertRaises(PermissionError):
            VoiceProduction.authorize_capture(explicit_consent=False)

    def test_curator_preserves_untrusted_default(self):
        curator = KnowledgeCurator()
        item = curator.add(title="note", content="content", source="user input")
        self.assertEqual(item.trust, "untrusted")
        self.assertFalse(item.approved)

    def test_identity_sign_verify_if_crypto_available(self):
        try:
            identity, private = generate_identity("yuki-test")
        except IdentityUnavailable:
            self.skipTest("optional cryptography package is not installed")
        envelope = sign_payload(identity, private, {"action": "test"})
        self.assertTrue(verify_envelope(envelope, trusted_fingerprints={identity.fingerprint}))
        self.assertFalse(verify_envelope(envelope, trusted_fingerprints=set()))


if __name__ == "__main__":
    unittest.main()
