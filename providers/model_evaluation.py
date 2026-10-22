"""Explicit, bounded model evaluation through Hugging Face's compatible inference router.

This module sends only caller-supplied benchmark prompts to the configured remote
model. It does not download weights, execute model code, or activate a model.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import quote

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_ALLOWED_HOST = "router.huggingface.co"
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_PROMPT_CHARS = 8_000
_MAX_CASES = 20
_MAX_TERMS = 20


class EvaluationError(RuntimeError):
    """A safe, user-facing evaluation error without upstream response details."""


def validate_cases(cases, *, max_cases=5):
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark must contain a non-empty cases list")
    if isinstance(max_cases, bool) or not isinstance(max_cases, int) or not 1 <= max_cases <= _MAX_CASES:
        raise ValueError("max_cases must be between 1 and 20")
    validated = []
    seen = set()
    for case in cases[:max_cases]:
        if not isinstance(case, dict):
            raise ValueError("each benchmark case must be an object")
        case_id, prompt = case.get("id"), case.get("prompt")
        terms = case.get("expected_terms", [])
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", case_id):
            raise ValueError("each benchmark case needs a safe, unique id")
        if case_id in seen:
            raise ValueError("benchmark case IDs must be unique")
        seen.add(case_id)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > _MAX_PROMPT_CHARS:
            raise ValueError("benchmark prompts must be non-empty and at most 8000 characters")
        if not isinstance(terms, list) or len(terms) > _MAX_TERMS:
            raise ValueError("expected_terms must be a list of at most 20 strings")
        if any(not isinstance(term, str) or not term.strip() or len(term) > 120 for term in terms):
            raise ValueError("expected_terms must contain short, non-empty strings")
        validated.append({"id": case_id, "prompt": prompt.strip(),
                          "expected_terms": [term.strip() for term in terms]})
    if not validated:
        raise ValueError("benchmark contains no cases within the requested limit")
    return validated


def score_response(response, expected_terms):
    """Case-insensitive expected-term coverage; a narrow proxy, not general quality."""
    if not isinstance(response, str):
        response = ""
    normalized = response.casefold()
    found = [term for term in expected_terms if term.casefold() in normalized]
    score = len(found) / len(expected_terms) if expected_terms else None
    return {"score": score, "matched_terms": found,
            "missing_terms": [term for term in expected_terms if term not in found],
            "metric": "expected_term_coverage" if expected_terms else "manual_review_required"}


class HuggingFaceChatClient:
    """Small HTTP client for a single explicitly named model on HF Inference Router."""

    def __init__(self, token, *, timeout=20, opener=None):
        if not isinstance(token, str) or not token.strip() or len(token) > 4096 or any(c in token for c in "\r\n"):
            raise ValueError("Set a valid HF_TOKEN environment variable.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            raise ValueError("timeout must be between 1 and 30 seconds")
        self._token = token.strip()
        self.timeout = timeout
        self._opener = opener or urllib.request.build_opener()

    def generate(self, model_id, prompt):
        if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
            raise ValueError("model_id must be an organization/model identifier")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > _MAX_PROMPT_CHARS:
            raise ValueError("prompt must be non-empty and at most 8000 characters")
        payload = json.dumps({
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": 512,
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://" + _ALLOWED_HOST + "/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": "Bearer " + self._token},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EvaluationError("Inference request failed; check model access, quota and connectivity.") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise EvaluationError("Inference response exceeded the 1 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            text = body["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise EvaluationError("Inference provider returned an unsupported response.") from exc
        if not isinstance(text, str) or not text.strip():
            raise EvaluationError("Inference provider returned no text response.")
        return text[:20_000]


def evaluate_model(model_id, cases, generate, *, max_cases=5):
    """Run a caller-supplied bounded benchmark; generate(prompt) performs inference."""
    if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
        raise ValueError("model_id must be an organization/model identifier")
    validated = validate_cases(cases, max_cases=max_cases)
    results = []
    for case in validated:
        started = time.monotonic()
        try:
            response = generate(case["prompt"])
            error = None
        except Exception:
            # Do not serialize exception strings; providers may include sensitive details.
            response, error = "", "inference_failed"
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        metrics = score_response(response, case["expected_terms"])
        results.append({
            "case_id": case["id"], "latency_ms": elapsed_ms,
            "status": "ok" if error is None else "error", "error": error,
            "score": metrics["score"], "metric": metrics["metric"],
            "matched_terms": metrics["matched_terms"],
            "missing_terms": metrics["missing_terms"],
        })
    scored = [item["score"] for item in results if item["score"] is not None and item["status"] == "ok"]
    latencies = [item["latency_ms"] for item in results if item["status"] == "ok"]
    return {
        "schema_version": 1,
        "model_id": model_id,
        "source": "huggingface_inference_router",
        "case_count": len(results),
        "successful_cases": sum(item["status"] == "ok" for item in results),
        "failed_cases": sum(item["status"] == "error" for item in results),
        "mean_expected_term_coverage": round(sum(scored) / len(scored), 4) if scored else None,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "results": results,
        "activation_policy": "manual_review_required",
        "interpretation": "Expected-term coverage is a narrow task-specific proxy, not a general quality, safety, or factuality score. Review outputs manually and compare candidates on the same benchmark.",
    }
