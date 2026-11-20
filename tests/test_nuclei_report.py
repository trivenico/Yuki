import json
import unittest

from defense.security import NucleiReportError, parse_nuclei_jsonl


def row(**overrides):
    value = {
        "template-id": "http.exposed-panel",
        "info": {"name": "Exposed Admin Panel", "severity": "medium"},
        "type": "http",
        "host": "https://example.test",
        "matched-at": "https://user:password@example.test/admin?token=secret#frag",
        "matcher-name": "status",
        "extracted-results": ["secret-value"],
        "timestamp": "2026-11-20T00:00:00Z",
        "request": "Authorization: Bearer secret",
        "response": "Set-Cookie: session=secret",
    }
    value.update(overrides)
    return value


class NucleiReportImportTests(unittest.TestCase):
    def test_imports_metadata_and_drops_sensitive_material(self):
        report = parse_nuclei_jsonl((json.dumps(row()) + "\n").encode())
        self.assertEqual(len(report.findings), 1)
        finding = report.findings[0]
        self.assertEqual(finding.template_id, "http.exposed-panel")
        self.assertEqual(finding.severity, "medium")
        self.assertEqual(finding.matched_location, "https://example.test/admin")
        self.assertEqual(finding.extracted_result_count, 1)
        self.assertFalse(finding.verified)
        self.assertEqual(finding.execution, "report_import_only")
        self.assertNotIn("secret", repr(finding))
        self.assertNotIn("Authorization", repr(finding))

    def test_strips_query_fragment_and_credentials_and_handles_ipv6(self):
        item = row(**{"matched-at": "https://u:p@[2001:db8::1]:8443/path?q=x#y"})
        finding = parse_nuclei_jsonl(json.dumps(item).encode()).findings[0]
        self.assertEqual(finding.matched_location, "https://[2001:db8::1]:8443/path")

    def test_skips_non_finding_lines_and_blank_lines(self):
        payload = b'{"type":"log","message":"start"}\n\n' + json.dumps(row()).encode()
        report = parse_nuclei_jsonl(payload)
        self.assertEqual(report.skipped_non_finding_lines, 1)
        self.assertEqual(len(report.findings), 1)

    def test_normalizes_unknown_severity_and_protocol(self):
        item = row()
        item["info"]["severity"] = "catastrophic"
        item["type"] = "custom-proto"
        finding = parse_nuclei_jsonl(json.dumps(item).encode()).findings[0]
        self.assertEqual(finding.severity, "unknown")
        self.assertEqual(finding.protocol, "unknown")

    def test_normalizes_null_severity_and_protocol(self):
        item = row()
        item["info"]["severity"] = None
        item["type"] = None
        finding = parse_nuclei_jsonl(json.dumps(item).encode()).findings[0]
        self.assertEqual(finding.severity, "unknown")
        self.assertEqual(finding.protocol, "unknown")

    def test_rejects_invalid_json_utf8_and_duplicate_keys(self):
        for payload in (b"{bad}\n", b"\xff", b'{"template-id":"a","template-id":"b"}'):
            with self.subTest(payload=payload), self.assertRaises(NucleiReportError):
                parse_nuclei_jsonl(payload)

    def test_rejects_missing_required_fields_and_bad_template_id(self):
        for item in ({"template-id": "bad id", "info": {"name": "x"}}, {"template-id": "x", "info": {}}):
            with self.subTest(item=item), self.assertRaises(NucleiReportError):
                parse_nuclei_jsonl(json.dumps(item).encode())

    def test_rejects_oversized_input_line_and_findings(self):
        item = json.dumps(row()).encode()
        with self.assertRaises(NucleiReportError):
            parse_nuclei_jsonl(item, max_report_bytes=10)
        with self.assertRaises(NucleiReportError):
            parse_nuclei_jsonl(item + b"\n" + item, max_findings=1)
        with self.assertRaises(NucleiReportError):
            parse_nuclei_jsonl(b'{"template-id":"' + b"x" * 17000 + b'"}')

    def test_sanitizes_target_url_and_rejects_target_injection(self):
        item = row()
        item["host"] = "https://user:pass@example.test/path?token=secret#frag"
        finding = parse_nuclei_jsonl(json.dumps(item).encode()).findings[0]
        self.assertEqual(finding.target, "https://example.test/path")
        item["host"] = "example.test?token=secret"
        with self.assertRaises(NucleiReportError):
            parse_nuclei_jsonl(json.dumps(item).encode())

    def test_rejects_bad_extracted_results(self):
        item = row()
        item["extracted-results"] = "not-a-list"
        with self.assertRaises(NucleiReportError):
            parse_nuclei_jsonl(json.dumps(item).encode())

    def test_rejects_non_bytes_and_invalid_limits(self):
        with self.assertRaises(NucleiReportError):
            parse_nuclei_jsonl(bytearray(b"{}"))
        with self.assertRaises(NucleiReportError):
            parse_nuclei_jsonl(b"{}", max_findings=0)


if __name__ == "__main__":
    unittest.main()
