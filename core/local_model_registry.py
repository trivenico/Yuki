"""Read-only discovery and conservative selection of local Ollama models.

This module never downloads, deletes, loads, or executes a model. It only reads
Ollama's loopback /api/tags endpoint and optional operator-supplied metadata.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class LocalModel:
    name: str
    digest: str | None
    size_bytes: int | None
    modified_at: str | None
    family: str | None
    parameter_size: str | None
    quantization: str | None
    license_status: str = "unknown"
    license_name: str | None = None
    license_source: str | None = None


@dataclass(frozen=True)
class ModelRecommendation:
    model_name: str
    roles: tuple[str, ...]
    estimated_memory_gib: float | None
    status: str
    rationale: str


class ModelRegistryError(RuntimeError):
    """Raised when local model inventory cannot be safely read."""


def _parameter_count_billions(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([bm])\b", value, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    return number if match.group(2).lower() == "b" else number / 1000.0


def _quantization_bytes_per_parameter(value: str | None) -> float:
    quant = (value or "").upper()
    if "Q2" in quant or "IQ2" in quant:
        return 0.40
    if "Q3" in quant or "IQ3" in quant:
        return 0.50
    if "Q4" in quant or "IQ4" in quant:
        return 0.65
    if "Q5" in quant:
        return 0.80
    if "Q6" in quant:
        return 0.95
    if "Q8" in quant:
        return 1.10
    if "F16" in quant or "FP16" in quant:
        return 2.10
    if "F32" in quant or "FP32" in quant:
        return 4.10
    return 1.20


class LocalModelRegistry:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        *,
        opener: Callable = urlopen,
        timeout: float = 2.0,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Local model registry only permits a loopback Ollama base URL.")
        if not 0 < timeout <= 5:
            raise ValueError("timeout must be greater than 0 and at most 5 seconds")
        if not 1 <= max_response_bytes <= 10_000_000:
            raise ValueError("max_response_bytes must be between 1 and 10000000")
        self.base_url = base_url.rstrip("/")
        self.opener = opener
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def discover(self) -> list[LocalModel]:
        request = Request(
            self.base_url + "/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                if getattr(response, "status", 200) != 200:
                    raise ModelRegistryError("Ollama model inventory endpoint returned a non-200 status.")
                raw = response.read(self.max_response_bytes + 1)
            if len(raw) > self.max_response_bytes:
                raise ModelRegistryError("Ollama model inventory exceeded the configured response limit.")
            payload = json.loads(raw)
        except ModelRegistryError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            raise ModelRegistryError(f"Could not read local Ollama inventory ({type(exc).__name__}).") from exc

        entries = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise ModelRegistryError("Ollama returned an invalid model inventory.")
        models: list[LocalModel] = []
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
                continue
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            size = item.get("size")
            models.append(LocalModel(
                name=item["name"].strip(),
                digest=item.get("digest") if isinstance(item.get("digest"), str) else None,
                size_bytes=size if isinstance(size, int) and size >= 0 else None,
                modified_at=item.get("modified_at") if isinstance(item.get("modified_at"), str) else None,
                family=details.get("family") if isinstance(details.get("family"), str) else None,
                parameter_size=details.get("parameter_size") if isinstance(details.get("parameter_size"), str) else None,
                quantization=details.get("quantization_level") if isinstance(details.get("quantization_level"), str) else None,
            ))
        return sorted(models, key=lambda model: model.name.casefold())

    @staticmethod
    def recommend(
        models: list[LocalModel],
        *,
        available_memory_gib: float | None,
        roles: tuple[str, ...] = ("general", "coding", "reasoning"),
    ) -> list[ModelRecommendation]:
        if available_memory_gib is not None and available_memory_gib <= 0:
            raise ValueError("available_memory_gib must be positive or None")
        recommendations: list[ModelRecommendation] = []
        for model in models:
            params_b = _parameter_count_billions(model.parameter_size)
            estimate = (
                round(params_b * _quantization_bytes_per_parameter(model.quantization) + 2.0, 1)
                if params_b is not None else None
            )
            if estimate is None:
                status = "unknown"
                rationale = "Parameter size or quantization is unavailable; inspect model metadata before selection."
            elif available_memory_gib is None:
                status = "unknown"
                rationale = f"Estimated model memory is about {estimate:.1f} GiB; available memory is unknown."
            elif estimate <= available_memory_gib * 0.70:
                status = "candidate"
                rationale = f"Estimated model memory is about {estimate:.1f} GiB, within a conservative 70% memory budget."
            else:
                status = "unlikely_to_fit"
                rationale = f"Estimated model memory is about {estimate:.1f} GiB, above the conservative 70% memory budget."
            recommendations.append(ModelRecommendation(
                model_name=model.name,
                roles=roles,
                estimated_memory_gib=estimate,
                status=status,
                rationale=rationale + " This is a heuristic, not a benchmark or guarantee; context, runtime overhead, and GPU offload change actual use.",
            ))
        return sorted(
            recommendations,
            key=lambda item: ({"candidate": 0, "unknown": 1, "unlikely_to_fit": 2}[item.status], item.model_name.casefold()),
        )

    @staticmethod
    def to_dict(models: list[LocalModel]) -> list[dict]:
        return [asdict(model) for model in models]
