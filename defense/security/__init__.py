"""Defensive security report importers; no active testing is provided."""
from defense.security.nuclei_report import (
    NucleiFinding, NucleiReport, NucleiReportError, parse_nuclei_jsonl,
)

__all__ = ["NucleiFinding", "NucleiReport", "NucleiReportError", "parse_nuclei_jsonl"]

from defense.security.osint_graph import (
    OsintEdge, OsintEvidence, OsintGraph, OsintImportError, OsintNode,
    OsintObservation, normalize_indicator, parse_osint_jsonl,
)

__all__ += [
    "OsintEdge", "OsintEvidence", "OsintGraph", "OsintImportError", "OsintNode",
    "OsintObservation", "normalize_indicator", "parse_osint_jsonl",
]
