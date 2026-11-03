import unittest

from core.skill_runtime_protocol import (
    SkillRuntimeProtocolError,
    build_runtime_response,
    parse_runtime_request,
    parse_runtime_response,
)


class SkillRuntimeProtocolTests(unittest.TestCase):
    def test_parses_exact_bounded_request(self):
        raw = (
            b'{"protocol_version":1,"invocation_id":"inv-1","skill_id":"report_builder",'
            b'"entrypoint":"package.entry:run","input_schema_sha256":"' + b"a"*64
            + b'","output_schema_sha256":"' + b"b"*64 + b'","arguments":{"format":"json"}}'
        )
        value = parse_runtime_request(raw, max_bytes=4096)
        self.assertEqual(value["arguments"], {"format": "json"})

    def test_rejects_duplicate_keys_nonfinite_and_oversized_input(self):
        prefix = b'{"protocol_version":1,"protocol_version":1}'
        with self.assertRaises(SkillRuntimeProtocolError):
            parse_runtime_request(prefix, max_bytes=4096)
        with self.assertRaises(SkillRuntimeProtocolError):
            parse_runtime_request(b'{"value":NaN}', max_bytes=4096)
        with self.assertRaises(SkillRuntimeProtocolError):
            parse_runtime_request(b"{}", max_bytes=1)

    def test_rejects_extra_request_fields(self):
        raw = (
            b'{"protocol_version":1,"invocation_id":"i","skill_id":"s","entrypoint":"m:f",'
            b'"input_schema_sha256":"' + b"a"*64
            + b'","output_schema_sha256":"' + b"b"*64 + b'","arguments":{},"command":"sh"}'
        )
        with self.assertRaises(SkillRuntimeProtocolError):
            parse_runtime_request(raw, max_bytes=4096)

    def test_response_is_bounded_and_bound_to_invocation(self):
        raw = build_runtime_response(
            invocation_id="inv-1", status="succeeded", output={"ok": True}, max_bytes=1024,
        )
        response = parse_runtime_response(raw, expected_invocation_id="inv-1", max_bytes=1024)
        self.assertEqual(response["output"], {"ok": True})
        with self.assertRaises(SkillRuntimeProtocolError):
            parse_runtime_response(raw, expected_invocation_id="inv-2", max_bytes=1024)
        with self.assertRaises(SkillRuntimeProtocolError):
            build_runtime_response(invocation_id="inv-1", status="succeeded", output={"x":"y"*2048}, max_bytes=128)


if __name__ == "__main__":
    unittest.main()
