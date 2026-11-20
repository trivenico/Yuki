"""Versioned, validated result envelopes for text-only specialist workers.

These envelopes preserve provenance boundaries: generated text is not treated as
verified evidence, and the worker records only the material actually supplied.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpecialistResult:
    specialist: str
    output: str
    schema_version: int = 1
    output_kind: str = "analysis_text"
    evidence_basis: str = "supplied_material_only"
    verified_externally: bool = False
    limitations: tuple[str, ...] = (
        "Generated output has not been independently verified.",
        "No external sources or tools were consulted by this worker.",
    )

    def to_payload(self) -> dict:
        validate_specialist_result(self)
        return {
            "schema_version": self.schema_version,
            "specialist": self.specialist,
            "output": self.output,
            "output_kind": self.output_kind,
            "provenance": {
                "evidence_basis": self.evidence_basis,
                "verified_externally": self.verified_externally,
            },
            "limitations": list(self.limitations),
            "execution": "text-only",
        }


def validate_specialist_result(result: SpecialistResult) -> None:
    if not isinstance(result, SpecialistResult):
        raise TypeError("result must be a SpecialistResult.")
    if type(result.schema_version) is not int or result.schema_version != 1:
        raise ValueError("unsupported specialist result schema version.")
    if not isinstance(result.specialist, str) or not result.specialist.strip() or len(result.specialist) > 80:
        raise ValueError("specialist must be bounded non-empty text.")
    if not isinstance(result.output, str) or not result.output.strip() or len(result.output) > 12_000 or "\x00" in result.output:
        raise ValueError("output must be bounded non-empty text.")
    if result.output_kind != "analysis_text":
        raise ValueError("unsupported specialist output kind.")
    if result.evidence_basis != "supplied_material_only":
        raise ValueError("unsupported evidence basis.")
    if type(result.verified_externally) is not bool or result.verified_externally:
        raise ValueError("text-only worker cannot assert external verification.")
    if not isinstance(result.limitations, tuple) or not 1 <= len(result.limitations) <= 8:
        raise ValueError("limitations must be a bounded tuple.")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 300 for item in result.limitations):
        raise ValueError("each limitation must be bounded non-empty text.")
