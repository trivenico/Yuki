import unittest

from core.memory_candidate_extractor import extract_memory_candidates


class MemoryCandidateExtractorTests(unittest.TestCase):
    def test_extracts_only_explicit_user_statements_with_provenance(self):
        messages = [
            {"role": "assistant", "message_id": "a1", "content": "I prefer concise replies."},
            {"role": "user", "message_id": "u1", "content": "I prefer concise technical explanations."},
            {"role": "user", "message_id": "u2", "content": "I work on Yuki local-first assistant."},
        ]
        result = extract_memory_candidates(messages, conversation_id="conversation-1")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["category"], "preference")
        self.assertEqual(result[0]["source"]["message_id"], "u1")
        self.assertTrue(result[0]["requires_owner_approval"])
        self.assertFalse(result[0]["memory_written"])
        self.assertEqual(result[1]["category"], "context")

    def test_ignores_sensitive_claims_and_credential_content(self):
        messages = [
            {"role": "user", "message_id": "u1", "content": "Remember that my API key is abc123."},
            {"role": "user", "message_id": "u2", "content": "I prefer secure local storage."},
        ]
        result = extract_memory_candidates(messages, conversation_id="conversation-1")
        self.assertEqual([item["source"]["message_id"] for item in result], ["u2"])

    def test_deduplicates_candidates_and_preserves_user_only_provenance(self):
        messages = [
            {"role": "user", "message_id": "u1", "content": "I prefer dark mode."},
            {"role": "user", "message_id": "u2", "content": "I prefer dark mode!"},
        ]
        result = extract_memory_candidates(messages, conversation_id="conversation-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"]["message_id"], "u1")

    def test_invalid_and_unbounded_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            extract_memory_candidates([], conversation_id="")
        with self.assertRaises(ValueError):
            extract_memory_candidates(
                [{"role": "user", "message_id": "u1", "content": "I prefer this."}],
                conversation_id="conversation-1", max_candidates=11,
            )
        with self.assertRaises(ValueError):
            extract_memory_candidates(
                [{"role": "user", "message_id": "u1", "content": "I prefer this."}] * 21,
                conversation_id="conversation-1",
            )
        with self.assertRaises(ValueError):
            extract_memory_candidates(
                [{"role": "user", "content": "I prefer this."}],
                conversation_id="conversation-1",
            )

    def test_does_not_infer_generic_statements(self):
        messages = [
            {"role": "user", "message_id": "u1", "content": "Yuki should remember all conversations."},
            {"role": "user", "message_id": "u2", "content": "I like it."},
        ]
        self.assertEqual(extract_memory_candidates(messages, conversation_id="conversation-1"), [])


if __name__ == "__main__":
    unittest.main()
