"""Read-only Ollama model inventory and conservative resource estimates.

This module lists models already installed on an Ollama server. It never pulls,
loads, deletes, or activates a model. Estimates are advisory; runtime fit must
be measured on the target hardware.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

_MAX_RESPONSE_BYTES = 2_000_000
_MAX_MODELS = 500
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")
_QUANT = re.compile(r"^[A-Za-z0-9_./-]{1,40}$")

@dataclass(frozen=True)
class InstalledModel:
    name: str
    digest: str | None
    size_bytes: int
    parameter_size: str | None
    quantization: str | None
    family: str | None

    def to_dict(self):
        return asdict(self)

def _optional_label(value):
    if isinstance(value, str) and value and len(value) <= 80 and _QUANT.fullmatch(value):
        return value
    return None

def parse_model_inventory(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("invalid_ollama_inventory")
    raw_models = payload["models"]
    if len(raw_models) > _MAX_MODELS:
        raise ValueError("ollama_model_limit_exceeded")
    result, seen = [], set()
    for raw in raw_models:
        if not isinstance(raw, dict):
            raise ValueError("invalid_ollama_model_record")
        name, size = raw.get("name"), raw.get("size")
        if not isinstance(name, str) or not _MODEL_NAME.fullmatch(name):
            raise ValueError("invalid_ollama_model_name")
        if name in seen:
            continue
        if type(size) is not int or not 0 < size <= 2**60:
            raise ValueError("invalid_ollama_model_size")
        details = raw.get("details", {})
        if not isinstance(details, dict):
            details = {}
        digest = raw.get("digest")
        if digest is not None and (not isinstance(digest, str) or len(digest) > 128):
            digest = None
        result.append(InstalledModel(
            name=name, digest=digest, size_bytes=size,
            parameter_size=_optional_label(details.get("parameter_size")),
            quantization=_optional_label(details.get("quantization_level")),
            family=_optional_label(details.get("family")),
        ))
        seen.add(name)
    return sorted(result, key=lambda item: item.name)

def estimate_requirements(model, *, runtime_overhead_ratio=0.20, gpu_offload_ratio=0.0):
    """Estimate host/GPU memory conservatively from on-disk size.

    These are planning estimates, not guaranteed fit predictions. GPU estimate
    is opt-in because Ollama's layer offload depends on model/runtime settings.
    """
    if not isinstance(model, InstalledModel):
        raise TypeError("model must be InstalledModel")
    if (isinstance(runtime_overhead_ratio, bool) or not isinstance(runtime_overhead_ratio, (int,float))
        or not 0.0 <= runtime_overhead_ratio <= 2.0):
        raise ValueError("invalid_runtime_overhead_ratio")
    if (isinstance(gpu_offload_ratio, bool) or not isinstance(gpu_offload_ratio, (int,float))
        or not 0.0 <= gpu_offload_ratio <= 1.0):
        raise ValueError("invalid_gpu_offload_ratio")
    estimated = int(model.size_bytes * (1.0 + runtime_overhead_ratio))
    gpu_estimated = int(estimated * gpu_offload_ratio)
    return {
        "model_id": model.name,
        "disk_size_bytes": model.size_bytes,
        "estimated_host_memory_bytes": estimated,
        "estimated_gpu_memory_bytes": gpu_estimated,
        "estimate_kind": "heuristic_from_installed_size",
        "confidence": "low",
        "requires_runtime_measurement": True,
        "automatic_activation": False,
    }

class OllamaModelInventory:
    """Read-only client for the Ollama /api/tags endpoint."""
    def __init__(self, base_url="http://127.0.0.1:11434", *, timeout=5.0, opener=None):
        if not isinstance(base_url,str) or not base_url.strip():
            raise ValueError("invalid_ollama_url")
        parsed=urlsplit(base_url.strip().rstrip("/"))
        host=parsed.hostname
        loopback=host in {"localhost","127.0.0.1","::1"}
        if parsed.scheme!="https" and not (parsed.scheme=="http" and loopback):
            raise ValueError("Ollama inventory requires HTTPS or loopback HTTP")
        if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid_ollama_url")
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 1<=timeout<=30:
            raise ValueError("invalid_timeout")
        self.url=base_url.strip().rstrip("/")+"/api/tags"
        self.timeout=float(timeout)
        self.opener=opener or urllib.request.build_opener()
    def list_installed(self):
        request=urllib.request.Request(self.url,headers={"Accept":"application/json"},method="GET")
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                raw=response.read(_MAX_RESPONSE_BYTES+1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError("Ollama inventory request rejected") from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise RuntimeError("Ollama inventory unavailable") from None
        if len(raw)>_MAX_RESPONSE_BYTES:
            raise RuntimeError("Ollama inventory response too large")
        try:
            payload=json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError,json.JSONDecodeError):
            raise RuntimeError("Ollama returned invalid inventory JSON") from None
        return parse_model_inventory(payload)
