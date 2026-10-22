"""Read-only Hugging Face Hub discovery with bounded requests and review-first output.

This module discovers public model metadata only. It never downloads weights,
imports remote code, installs dependencies, or changes Yuki's active provider.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HUB_API = "https://huggingface.co/api/models"
_ALLOWED_TASKS = frozenset({
    "text-generation", "text2text-generation", "conversational",
    "feature-extraction", "image-text-to-text", "automatic-speech-recognition",
})
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_LICENSE_TAG = re.compile(r"^license:(.+)$", re.IGNORECASE)
_MAX_RESPONSE_BYTES = 1_500_000


class DiscoveryError(RuntimeError):
    """Raised when the Hub response is invalid or discovery cannot complete."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep optional bearer credentials and requests on the fixed Hub endpoint."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _bounded_int(value, default=0):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


def _clean_text(value, limit=240):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split()).strip()
    return value[:limit] if value else None


def normalize_model(item):
    """Normalize one Hub model object to safe, reviewable metadata."""
    if not isinstance(item, dict):
        return None
    model_id = item.get("id") or item.get("modelId")
    if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
        return None
    tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    tags = [_clean_text(tag, 100) for tag in tags if isinstance(tag, str)]
    tags = [tag for tag in tags if tag]
    license_value = _clean_text(item.get("license"), 100)
    if not license_value:
        license_value = next((match.group(1) for tag in tags
                              if (match := _LICENSE_TAG.fullmatch(tag))), None)
    task = _clean_text(item.get("pipeline_tag"), 80)
    license_known = bool(license_value)
    return {
        "model_id": model_id,
        "url": "https://huggingface.co/" + model_id,
        "task": task,
        "license": license_value,
        "license_known": license_known,
        "downloads": _bounded_int(item.get("downloads")),
        "likes": _bounded_int(item.get("likes")),
        "last_modified": _clean_text(item.get("lastModified"), 40),
        "tags": tags[:40],
        "review_status": "needs_human_review",
        "automatic_activation": False,
        "review_flags": (["license_not_reported"] if not license_known else [])
                        + (["task_not_allowlisted"] if task not in _ALLOWED_TASKS else []),
    }


class HuggingFaceDiscovery:
    """Discover public Hub model metadata with an allow-listed HTTPS endpoint."""

    def __init__(self, *, timeout=10, max_results=20, opener=None):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            raise ValueError("timeout must be between 1 and 30 seconds")
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")
        self.timeout = timeout
        self.max_results = max_results
        self._opener = opener or urllib.request.build_opener(_RejectRedirects())

    def discover(self, *, task="text-generation", sort="downloads", token=None):
        if task not in _ALLOWED_TASKS:
            raise ValueError("task is not allow-listed for discovery")
        if sort not in {"downloads", "likes", "lastModified"}:
            raise ValueError("sort must be downloads, likes, or lastModified")
        params = urllib.parse.urlencode({
            "filter": task, "sort": sort, "direction": "-1",
            "limit": str(self.max_results), "full": "false",
        })
        url = HUB_API + "?" + params
        headers = {"Accept": "application/json", "User-Agent": "Yuki-ModelDiscovery/1.0"}
        if token:
            if not isinstance(token, str) or len(token) > 4096 or any(c in token for c in "\r\n"):
                raise ValueError("Hugging Face token format is invalid")
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DiscoveryError("Hugging Face Hub metadata request failed") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise DiscoveryError("Hugging Face Hub response exceeded the size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DiscoveryError("Hugging Face Hub returned invalid JSON") from exc
        if not isinstance(payload, list):
            raise DiscoveryError("Hugging Face Hub returned an unsupported response")
        models = []
        seen = set()
        for entry in payload[:self.max_results]:
            model = normalize_model(entry)
            if model and model["model_id"] not in seen:
                seen.add(model["model_id"])
                models.append(model)
        return {
            "source": "huggingface_hub_public_model_metadata",
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "task": task,
            "sort": sort,
            "count": len(models),
            "models": models,
            "activation_policy": "manual_review_required",
        }
