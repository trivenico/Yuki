"""Skill manifest representation for bounded, schema-validated skills."""
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class SkillManifest:
    skill_id: str
    version: str
    entrypoint: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    capabilities: List[str]
    risk: str = "low"
    timeout_seconds: int = 30
    max_output_bytes: int = 1024 * 1024

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillManifest":
        return cls(
            skill_id=data["skill_id"],
            version=data.get("version", "1.0.0"),
            entrypoint=data.get("entrypoint", ""),
            input_schema=data.get("input_schema", {}),
            output_schema=data.get("output_schema", {}),
            capabilities=data.get("capabilities", []),
            risk=data.get("risk", "low"),
            timeout_seconds=data.get("timeout_seconds", 30),
            max_output_bytes=data.get("max_output_bytes", 1024 * 1024),
        )
