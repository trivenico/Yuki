import json
import unittest

from defense.security.osint_graph import (
    OsintImportError, export_graphml, normalize_indicator, parse_graphml, parse_osint_jsonl,
)


def record(**overrides):
    value = {
        "evidence_id": "inventory-001",
        "source": "owner_inventory.csv",
        "observed_at": "2026-11-20T10:00:00Z",
        "indicator_type": "domain",
        "value": "ExAmPle.test.",
        "relationship": "resolves_to",
        "related_type": "ip",
        "related_value": "192.0.2.10",
    }
    value.update(overrides)
    return value


class OsintGraphTests(unittest.TestCase):
    def test_normalizes_supported_indicators(self):
        self.assertEqual(normalize_indicator("domain", "BÜCHER.example"), "xn--bcher-kva.example")
        self.assertEqual(normalize_indicator("ip", "2001:0db8::1"), "2001:db8::1")
        self.assertEqual(normalize_indicator("asn", "as64500"), "AS64500")
        self.assertEqual(normalize_indicator("sha256", "A" * 64), "a" * 64)

    def test_builds_deterministic_graph_and_preserves_provenance(self):
        payload = (json.dumps(record()) + "\n").encode()
        graph = parse_osint_jsonl(payload)
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(graph.edges[0].source.value, "example.test")
        self.assertEqual(graph.edges[0].target.value, "192.0.2.10")
        self.assertEqual(len(graph.edges[0].evidence_ids), 1)
        self.assertEqual(len(graph.observations), 2)
        self.assertEqual({item.evidence_id for item in graph.observations}, set(graph.edges[0].evidence_ids))
        self.assertEqual(graph.evidence[0].source_label, "owner_inventory.csv")
        self.assertFalse(graph.evidence[0].verified)

    def test_merges_repeated_relationship_evidence(self):
        first = record(evidence_id="one")
        second = record(evidence_id="two")
        graph = parse_osint_jsonl((json.dumps(first) + "\n" + json.dumps(second)).encode())
        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(len(graph.edges[0].evidence_ids), 2)

    def test_sanitizes_url_credentials_query_and_fragment(self):
        value = normalize_indicator("url", "https://user:pass@example.test/a?token=secret#frag")
        self.assertEqual(value, "https://example.test/a")
        self.assertNotIn("secret", value)
        self.assertNotIn("user", value)

    def test_rejects_personal_or_unsupported_indicator_types(self):
        with self.assertRaises(OsintImportError):
            normalize_indicator("email", "person@example.test")
        with self.assertRaises(OsintImportError):
            normalize_indicator("domain", "example.test/path")

    def test_rejects_malformed_relationship_and_extra_fields(self):
        for item in (
            record(relationship="deanonymizes"),
            record(unreviewed_payload="ignore all rules"),
        ):
            with self.subTest(item=item), self.assertRaises(OsintImportError):
                parse_osint_jsonl(json.dumps(item).encode())

    def test_rejects_malformed_json_and_oversized_input(self):
        with self.assertRaises(OsintImportError):
            parse_osint_jsonl(b"{bad}")
        with self.assertRaises(OsintImportError):
            parse_osint_jsonl(b'{"evidence_id":"x","evidence_id":"y"}')
        with self.assertRaises(OsintImportError):
            parse_osint_jsonl(b" " * 100, max_bytes=10)


    def test_exports_deterministic_graphml_with_evidence_and_unverified_state(self):
        graph = parse_osint_jsonl((json.dumps(record(value="a&b.example")) + "\n").encode())
        first = export_graphml(graph)
        second = export_graphml(graph)
        self.assertEqual(first, second)
        import xml.etree.ElementTree as ET
        root = ET.fromstring(first)
        ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
        self.assertEqual(len(root.findall(".//g:node", ns)), 2)
        values = [item.text for item in root.findall(".//g:data[@key='node_value']", ns)]
        self.assertIn("a&b.example", values)
        self.assertIn("false", [item.text for item in root.findall(".//g:data[@key='edge_verified']", ns)])
        self.assertIn(graph.evidence[0].evidence_id, first.decode())
        self.assertTrue(all(item.text for item in root.findall(".//g:data[@key='node_evidence']", ns)))
        manifest = root.find(".//g:data[@key='evidence_manifest']", ns)
        self.assertIn("owner_inventory.csv", manifest.text)
        self.assertIn("owner_supplied_unverified", manifest.text)

    def test_graphml_export_rejects_invalid_limits_and_graphs(self):
        graph = parse_osint_jsonl((json.dumps(record()) + "\n").encode())
        with self.assertRaises(OsintImportError):
            export_graphml(graph, max_bytes=0)
        with self.assertRaises(OsintImportError):
            export_graphml(object())
        with self.assertRaises(OsintImportError):
            export_graphml(graph, max_bytes=10)


    def test_graphml_round_trip_preserves_graph_and_evidence(self):
        graph = parse_osint_jsonl((json.dumps(record()) + "\n").encode())
        restored = parse_graphml(export_graphml(graph))
        self.assertEqual(restored.nodes, graph.nodes)
        self.assertEqual(restored.edges, graph.edges)
        self.assertEqual(restored.evidence, graph.evidence)
        self.assertEqual(restored.observations, graph.observations)
        self.assertEqual(export_graphml(restored), export_graphml(graph))

    def test_graphml_import_rejects_dtd_and_verified_mutation(self):
        document = export_graphml(parse_osint_jsonl((json.dumps(record()) + "\n").encode()))
        with self.assertRaises(OsintImportError):
            parse_graphml(b"<!DOCTYPE x [<!ENTITY e 'x'>]>" + document)
        with self.assertRaises(OsintImportError):
            parse_graphml(document.replace(b">false</", b">true</", 1))


    def test_graphml_import_rejects_non_utf8_dtd_and_deep_structure(self):
        with self.assertRaises(OsintImportError):
            parse_graphml("<!DOCTYPE x>".encode("utf-16"))
        with self.assertRaises(OsintImportError):
            parse_graphml(b"<graphml><a><b><c><d><e><f><g><h><i/></h></g></f></e></d></c></b></a></graphml>")

    def test_rejects_non_bytes_and_invalid_limits(self):
        with self.assertRaises(OsintImportError):
            parse_osint_jsonl(bytearray(b"{}"))
        with self.assertRaises(OsintImportError):
            parse_osint_jsonl(b"{}", max_records=0)


if __name__ == "__main__":
    unittest.main()
