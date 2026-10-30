import json
import unittest

from automation.conversation_importers import (
    ConversationImportError,
    parse_chatgpt_export,
    parse_normalized_jsonl,
)


class ConversationImporterTests(unittest.TestCase):
    def test_chatgpt_export_normalizes_mapping_messages(self):
        payload = [{
            "title": "Example",
            "mapping": {
                "node-1": {"message": {
                    "id": "msg-1", "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["Hello Yuki"]},
                    "create_time": 1700000000,
                }},
                "node-2": {"message": {
                    "id": "msg-2", "author": {"role": "assistant"},
                    "content": {"parts": ["Hello back"]},
                    "create_time": 1700000001,
                }},
                "node-3": {"message": {
                    "id": "msg-3", "author": {"role": "tool"},
                    "content": {"parts": [{"image": "ignored"}]},
                }},
            },
        }]
        records = parse_chatgpt_export(json.dumps(payload))
        self.assertEqual([item["id"] for item in records], ["msg-1", "msg-2"])
        self.assertEqual(records[0]["content"], "Hello Yuki")
        self.assertEqual(records[0]["role"], "user")
        self.assertTrue(records[0]["created_at"].startswith("2023-"))

    def test_jsonl_normalization_and_stable_fallback_id(self):
        records = parse_normalized_jsonl(
            '{"role":"assistant","content":"A useful note"}\n'
            '{"id":"m2","role":"user","content":["question","follow-up"]}\n'
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(len(records[0]["id"]), 64)
        self.assertEqual(records[1]["content"], "question\nfollow-up")

    def test_rejects_invalid_roots_and_malformed_jsonl(self):
        with self.assertRaises(ConversationImportError):
            parse_chatgpt_export('{"not":"an array"}')
        with self.assertRaises(ConversationImportError):
            parse_normalized_jsonl('{"role":')
        
    def test_enforces_payload_and_record_limits(self):
        with self.assertRaises(ConversationImportError):
            parse_normalized_jsonl("x" * (25 * 1024 * 1024 + 1))
        oversized = {"id":"x","role":"user","content":"a" * 12_001}
        self.assertEqual(parse_normalized_jsonl(json.dumps(oversized)), [])

    def test_ignores_non_text_multimodal_payloads(self):
        payload = [{"mapping":{"x":{"message":{
            "id":"m1","author":{"role":"user"},
            "content":{"parts":[{"image":"private binary reference"}]},
        }}}}]
        self.assertEqual(parse_chatgpt_export(json.dumps(payload)), [])
