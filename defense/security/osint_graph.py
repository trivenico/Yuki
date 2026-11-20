"""Offline, provenance-aware OSINT graph for owner-supplied asset observations.

No network access, scraping, enrichment, people search, or identity resolution occurs.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

_MAX_BYTES = 2 * 1024 * 1024
_MAX_LINES = 5000
_MAX_LINE_BYTES = 8192
_MAX_TEXT = 512
_TYPES = frozenset({"domain", "ip", "url", "asn", "sha256"})
_RELATIONSHIPS = frozenset({"resolves_to", "hosts", "references", "shares_certificate", "observed_with"})


class OsintImportError(ValueError):
    """Raised when an OSINT observation import is malformed or unsafe."""


@dataclass(frozen=True, order=True)
class OsintNode:
    indicator_type: str
    value: str


@dataclass(frozen=True)
class OsintObservation:
    node: OsintNode
    evidence_id: str


@dataclass(frozen=True)
class OsintEdge:
    source: OsintNode
    relationship: str
    target: OsintNode
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class OsintEvidence:
    evidence_id: str
    source_label: str
    observed_at: str | None
    provenance: str = "owner_supplied_unverified"
    verified: bool = False


@dataclass(frozen=True)
class OsintGraph:
    nodes: tuple[OsintNode, ...]
    edges: tuple[OsintEdge, ...]
    evidence: tuple[OsintEvidence, ...]
    observations: tuple[OsintObservation, ...]
    skipped_records: int = 0


def _text(value: object, field: str, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise OsintImportError(f"invalid {field}")
    value = value.strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise OsintImportError(f"invalid {field}")
    return value


def normalize_indicator(kind: str, value: str) -> str:
    """Normalize one supported technical asset indicator; never resolves names."""
    if not isinstance(kind, str) or kind not in _TYPES:
        raise OsintImportError("unsupported indicator type")
    raw = _text(value, "indicator value", 2048)
    if kind == "ip":
        try:
            return ipaddress.ip_address(raw).compressed
        except ValueError as exc:
            raise OsintImportError("invalid IP address") from exc
    if kind == "domain":
        candidate = raw.rstrip(".")
        if not candidate or "/" in candidate or "@" in candidate or ":" in candidate:
            raise OsintImportError("invalid domain")
        try:
            normalized = candidate.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise OsintImportError("invalid domain") from exc
        if len(normalized) > 253 or any(
            not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in normalized.split(".")
        ):
            raise OsintImportError("invalid domain")
        return normalized
    if kind == "url":
        try:
            parsed = urlsplit(raw)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
                raise ValueError
            host = normalize_indicator("ip", parsed.hostname) if ":" in parsed.hostname or re.fullmatch(r"[0-9.]+", parsed.hostname) else normalize_indicator("domain", parsed.hostname)
            port = parsed.port
            if port is not None and not 1 <= port <= 65535:
                raise ValueError
            netloc = f"[{host}]" if ":" in host else host
            if port is not None:
                netloc += f":{port}"
            path = parsed.path[:1024] or "/"
            # Drop credentials, query strings, and fragments by construction.
            return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))
        except (ValueError, UnicodeError) as exc:
            raise OsintImportError("invalid URL") from exc
    if kind == "asn":
        match = re.fullmatch(r"(?:AS)?([0-9]{1,10})", raw, re.IGNORECASE)
        if not match or int(match.group(1)) > 4294967295:
            raise OsintImportError("invalid ASN")
        return f"AS{int(match.group(1))}"
    if not re.fullmatch(r"[A-Fa-f0-9]{64}", raw):
        raise OsintImportError("only SHA-256 fingerprints are supported")
    return raw.lower()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OsintImportError("duplicate JSON object key")
        result[key] = value
    return result


def parse_osint_jsonl(payload: bytes, *, max_bytes: int = _MAX_BYTES,
                      max_records: int = _MAX_LINES) -> OsintGraph:
    """Build a graph from JSONL asset observations; input is data, never instructions."""
    if not isinstance(payload, bytes):
        raise OsintImportError("input must be immutable bytes")
    if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_BYTES:
        raise OsintImportError("invalid byte limit")
    if type(max_records) is not int or not 1 <= max_records <= _MAX_LINES:
        raise OsintImportError("invalid record limit")
    if len(payload) > max_bytes:
        raise OsintImportError("input exceeds byte limit")
    try:
        decoded = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OsintImportError("input must be UTF-8") from exc

    nodes: set[OsintNode] = set()
    edge_evidence: dict[tuple[OsintNode, str, OsintNode], set[str]] = {}
    observations: set[OsintObservation] = set()
    evidence: dict[str, OsintEvidence] = {}
    skipped = 0
    count = 0
    for line in decoded.splitlines():
        if not line.strip():
            continue
        count += 1
        if count > max_records:
            raise OsintImportError("record count exceeds limit")
        if len(line.encode("utf-8")) > _MAX_LINE_BYTES:
            raise OsintImportError("record line exceeds byte limit")
        try:
            row = json.loads(line, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise OsintImportError("malformed JSON record") from exc
        if not isinstance(row, dict) or set(row) - {
            "evidence_id", "source", "observed_at", "indicator_type", "value",
            "relationship", "related_type", "related_value"
        }:
            raise OsintImportError("record must be an object with supported fields")
        evidence_id = _text(row.get("evidence_id"), "evidence id", 128)
        source = _text(row.get("source"), "source label", 128)
        observed_at = row.get("observed_at")
        if observed_at is not None:
            observed_at = _text(observed_at, "observation timestamp", 80)
        digest = hashlib.sha256((evidence_id + "\0" + source + "\0" + (observed_at or "")).encode()).hexdigest()
        if digest in evidence:
            raise OsintImportError("duplicate evidence record")
        evidence[digest] = OsintEvidence(digest, source, observed_at)

        kind = _text(row.get("indicator_type"), "indicator type", 16).lower()
        value = normalize_indicator(kind, _text(row.get("value"), "indicator value", 2048))
        left = OsintNode(kind, value)
        nodes.add(left)
        observations.add(OsintObservation(left, digest))
        relationship = row.get("relationship")
        if relationship is None:
            skipped += 1
            continue
        if not isinstance(relationship, str) or relationship not in _RELATIONSHIPS:
            raise OsintImportError("unsupported relationship")
        right_kind = _text(row.get("related_type"), "related indicator type", 16).lower()
        right_value = normalize_indicator(right_kind, _text(row.get("related_value"), "related indicator value", 2048))
        right = OsintNode(right_kind, right_value)
        nodes.add(right)
        observations.add(OsintObservation(right, digest))
        key = (left, relationship, right)
        edge_evidence.setdefault(key, set()).add(digest)

    edges = tuple(
        OsintEdge(source, relation, target, tuple(sorted(ids)))
        for (source, relation, target), ids in sorted(
            edge_evidence.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])
        )
    )
    ordered_observations = tuple(sorted(observations, key=lambda item: (item.node, item.evidence_id)))
    return OsintGraph(tuple(sorted(nodes)), edges, tuple(evidence[key] for key in sorted(evidence)),
                      ordered_observations, skipped)

def export_graphml(graph: OsintGraph, *, max_bytes: int = 4 * 1024 * 1024) -> bytes:
    """Serialize a bounded graph to deterministic GraphML; no claims are upgraded.

    Node and edge identifiers are generated from sorted graph order. Evidence IDs,
    relationship names, and unverified status are retained as GraphML data fields.
    """
    import xml.etree.ElementTree as ET

    if type(graph) is not OsintGraph:
        raise OsintImportError("graph must be an OsintGraph")
    if type(max_bytes) is not int or not 1 <= max_bytes <= 4 * 1024 * 1024:
        raise OsintImportError("invalid GraphML byte limit")
    if len(graph.nodes) > 10_000 or len(graph.edges) > 20_000 or len(graph.evidence) > 20_000:
        raise OsintImportError("graph exceeds export limits")
    if tuple(sorted(set(graph.nodes))) != graph.nodes:
        raise OsintImportError("graph nodes must be unique and sorted")
    if tuple(sorted(graph.edges, key=lambda edge: (edge.source, edge.relationship, edge.target))) != graph.edges:
        raise OsintImportError("graph edges must be sorted")
    node_set = set(graph.nodes)
    if any(edge.source not in node_set or edge.target not in node_set for edge in graph.edges):
        raise OsintImportError("edge references an unknown node")
    evidence_ids = {item.evidence_id for item in graph.evidence}
    if any(not set(edge.evidence_ids).issubset(evidence_ids) for edge in graph.edges):
        raise OsintImportError("edge references unknown evidence")

    namespace = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", namespace)
    q = lambda name: f"{{{namespace}}}{name}"
    root = ET.Element(q("graphml"))
    key_specs = (
        ("node_type", "node", "indicator_type"),
        ("node_value", "node", "value"),
        ("node_evidence", "node", "evidence_ids"),
        ("evidence_manifest", "graph", "evidence_manifest"),
        ("edge_relationship", "edge", "relationship"),
        ("edge_evidence", "edge", "evidence_ids"),
        ("edge_verified", "edge", "verified"),
    )
    for key_id, scope, name in key_specs:
        ET.SubElement(root, q("key"), {"id": key_id, "for": scope, "attr.name": name, "attr.type": "string"})
    graph_element = ET.SubElement(root, q("graph"), {"id": "asset_observations", "edgedefault": "directed"})
    manifest = [
        {"evidence_id": item.evidence_id, "source": item.source_label,
         "observed_at": item.observed_at, "provenance": item.provenance,
         "verified": item.verified}
        for item in graph.evidence
    ]
    ET.SubElement(graph_element, q("data"), {"key": "evidence_manifest"}).text = json.dumps(
        manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    node_evidence: dict[OsintNode, list[str]] = {}
    for observation in graph.observations:
        node_evidence.setdefault(observation.node, []).append(observation.evidence_id)
    node_ids = {node: f"n{index}" for index, node in enumerate(graph.nodes)}
    for node in graph.nodes:
        element = ET.SubElement(graph_element, q("node"), {"id": node_ids[node]})
        ET.SubElement(element, q("data"), {"key": "node_type"}).text = node.indicator_type
        ET.SubElement(element, q("data"), {"key": "node_value"}).text = node.value
        ET.SubElement(element, q("data"), {"key": "node_evidence"}).text = ",".join(
            sorted(node_evidence.get(node, []))
        )
    for index, edge in enumerate(graph.edges):
        element = ET.SubElement(graph_element, q("edge"), {
            "id": f"e{index}", "source": node_ids[edge.source], "target": node_ids[edge.target],
        })
        ET.SubElement(element, q("data"), {"key": "edge_relationship"}).text = edge.relationship
        ET.SubElement(element, q("data"), {"key": "edge_evidence"}).text = ",".join(edge.evidence_ids)
        ET.SubElement(element, q("data"), {"key": "edge_verified"}).text = "false"
    output = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(output) > max_bytes:
        raise OsintImportError("GraphML output exceeds byte limit")
    return output

