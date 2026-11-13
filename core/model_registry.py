"""Integrity-checked registry for Yuki-owned model artifacts."""
from __future__ import annotations
import hashlib, json, re
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_ORIGINS = {"random_init", "yuki_checkpoint", "third_party_checkpoint", "yuki_finetune"}
_ALLOWED_FAMILIES = {"language", "embedding", "reranker", "vision", "speech", "classifier"}
_REQUIRED_ARTIFACTS = {"weights", "tokenizer", "config"}

class ModelRegistryError(ValueError):
    """Raised when a model manifest or artifact fails validation."""

def _safe_file(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ModelRegistryError(f"{label} path must be a non-empty relative path.")
    try:
        candidate = (root / relative).resolve(strict=True)
    except OSError as exc:
        raise ModelRegistryError(f"{label} file does not exist or cannot be resolved.") from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ModelRegistryError(f"{label} path escapes the model registry root.") from exc
    if not candidate.is_file():
        raise ModelRegistryError(f"{label} must reference a regular file.")
    return candidate

def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

class ModelRegistry:
    """Load and verify local Yuki releases; never download or execute model files."""
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise ModelRegistryError("Model registry root must be an existing directory.")

    def load(self, manifest_path: str | Path) -> dict:
        manifest = _safe_file(self.root, str(manifest_path), "Manifest")
        if manifest.stat().st_size > 64 * 1024:
            raise ModelRegistryError("Model manifest exceeds the 64 KiB limit.")
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelRegistryError("Model manifest is not valid UTF-8 JSON.") from exc
        required = {"schema_version","model_id","version","family","architecture","origin","license","training_data","tasks","artifacts"}
        optional = {"lineage"}
        if not isinstance(data, dict) or not required.issubset(data) or set(data) - required - optional:
            raise ModelRegistryError("Model manifest has missing or unsupported fields.")
        if isinstance(data["schema_version"], bool) or data["schema_version"] != 1:
            raise ModelRegistryError("Unsupported model manifest schema version.")
        if not isinstance(data["model_id"], str) or not _ID.fullmatch(data["model_id"]):
            raise ModelRegistryError("model_id is invalid.")
        if not isinstance(data["version"], str) or not _VERSION.fullmatch(data["version"]):
            raise ModelRegistryError("version must use semantic versioning.")
        if not isinstance(data["family"], str) or data["family"] not in _ALLOWED_FAMILIES:
            raise ModelRegistryError("Unsupported model family.")
        if not isinstance(data["architecture"], str) or not data["architecture"].strip() or len(data["architecture"]) > 120:
            raise ModelRegistryError("architecture must be a non-empty identifier.")
        if not isinstance(data["origin"], str) or data["origin"] not in _ALLOWED_ORIGINS:
            raise ModelRegistryError("Unsupported model origin.")
        lineage = data.get("lineage")
        if data["origin"] in {"third_party_checkpoint", "yuki_finetune"}:
            if not isinstance(lineage, dict) or set(lineage) != {
                "source_repo", "revision", "upstream_license", "source_url", "modifications"
            }:
                raise ModelRegistryError("Third-party or fine-tuned models require complete lineage metadata.")
            for field in ("source_repo", "revision", "upstream_license", "source_url"):
                value = lineage[field]
                if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                    raise ModelRegistryError(f"lineage.{field} must be non-empty text.")
            if not lineage["source_url"].startswith("https://"):
                raise ModelRegistryError("lineage.source_url must use HTTPS.")
            if not isinstance(lineage["modifications"], list) or len(lineage["modifications"]) > 64:
                raise ModelRegistryError("lineage.modifications must be a list of at most 64 entries.")
            if any(not isinstance(item, str) or not item.strip() or len(item) > 500
                   for item in lineage["modifications"]):
                raise ModelRegistryError("lineage.modifications entries must be non-empty text.")
        elif lineage is not None:
            raise ModelRegistryError("First-party model origins cannot declare third-party lineage.")
        for field in ("license","training_data"):
            if not isinstance(data[field], str) or not data[field].strip() or len(data[field]) > 4000:
                raise ModelRegistryError(f"{field} must document provenance.")
        tasks = data["tasks"]
        if (not isinstance(tasks,list) or not tasks or len(tasks)>32 or
            any(not isinstance(x,str) or not x.strip() or len(x)>80 for x in tasks) or len(set(tasks)) != len(tasks)):
            raise ModelRegistryError("tasks must be a non-empty list of unique task names.")
        artifacts = data["artifacts"]
        if not isinstance(artifacts,dict) or set(artifacts) != _REQUIRED_ARTIFACTS:
            raise ModelRegistryError("artifacts must define weights, tokenizer, and config.")
        verified = {}
        for name, descriptor in artifacts.items():
            if not isinstance(descriptor,dict) or set(descriptor) != {"path","sha256"}:
                raise ModelRegistryError(f"{name} artifact descriptor is invalid.")
            if not isinstance(descriptor["sha256"],str) or not _SHA256.fullmatch(descriptor["sha256"]):
                raise ModelRegistryError(f"{name} SHA-256 digest is invalid.")
            path = _safe_file(self.root, descriptor["path"], name)
            actual = _digest(path)
            if actual != descriptor["sha256"]:
                raise ModelRegistryError(f"{name} artifact integrity check failed.")
            verified[name] = {"path":str(path),"sha256":actual,"size_bytes":path.stat().st_size}
        return {**data,"manifest_path":str(manifest),"artifacts":verified,"verified":True}
