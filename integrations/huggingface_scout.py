"""Discover potentially useful Hugging Face Hub releases; never install or execute them.

Uses the public Hub REST API with fixed HTTPS hosts and stdlib only. Results are
candidates for human review, not endorsements or verified license/safety decisions.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_BASE = "https://huggingface.co/api"
_DEFAULT_QUERIES = {
    "models": ("agent", "tool calling", "coding", "reasoning", "embedding", "reranker", "vision language", "speech"),
    "datasets": ("agent", "code", "instruction", "evaluation"),
    "spaces": ("agent", "coding", "llm", "workflow"),
}
_RELEVANCE = {
    "agent": 5, "agents": 5, "tool-calling": 5, "function-calling": 5,
    "code": 4, "coding": 4, "reasoning": 4, "instruct": 3,
    "embedding": 3, "text-generation": 3, "text-to-speech": 2,
    "automatic-speech-recognition": 2, "image-text-to-text": 2,
    "reranker": 3, "gguf": 2, "mlx": 2, "onnx": 2,
}

@dataclass(frozen=True)
class Candidate:
    repo_id: str
    kind: str
    url: str
    last_modified: str | None
    downloads: int
    likes: int
    tags: tuple[str, ...]
    pipeline_tag: str | None
    license: str | None
    gated: bool
    relevance_score: int
    review_status: str
    query_matches: tuple[str, ...]

    def to_dict(self):
        return asdict(self)

class HuggingFaceScout:
    """Search public Hub model/dataset/Space metadata with bounded requests."""
    def __init__(self, *, timeout_seconds: float = 8.0, max_results_per_query: int = 20,
                 user_agent: str = "Yuki-HuggingFace-Scout/1.0"):
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30")
        if not 1 <= max_results_per_query <= 100:
            raise ValueError("max_results_per_query must be between 1 and 100")
        self.timeout_seconds = timeout_seconds
        self.max_results_per_query = max_results_per_query
        self.user_agent = user_agent[:120]

    def _get_json(self, url: str):
        # Only fixed Hub API hosts are queried; callers cannot supply an arbitrary URL.
        if not url.startswith(_BASE + "/models?") and not url.startswith(_BASE + "/datasets?") and not url.startswith(_BASE + "/spaces?"):
            raise ValueError("Unsupported Hub API endpoint")
        request = Request(url, headers={"Accept": "application/json", "User-Agent": self.user_agent})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise RuntimeError(f"Hugging Face Hub returned HTTP {response.status}")
                payload = response.read(4_000_001)
            if len(payload) > 4_000_000:
                raise RuntimeError("Hugging Face response exceeded the 4 MB limit")
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, list):
                raise RuntimeError("Unexpected Hugging Face Hub response format")
            return data
        except HTTPError as exc:
            raise RuntimeError(f"Hugging Face Hub returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Hugging Face Hub request failed or timed out") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Hugging Face Hub returned invalid JSON") from exc

    @staticmethod
    def _metadata(kind, item, query):
        repo_id = item.get("id") or item.get("modelId") or item.get("name")
        if not isinstance(repo_id, str) or "/" not in repo_id or len(repo_id) > 200:
            return None
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        tags = tuple(str(tag)[:100] for tag in tags[:100] if isinstance(tag, str))
        card = item.get("cardData") or {}
        if not isinstance(card, dict):
            card = {}
        license_name = card.get("license")
        if not license_name:
            license_name = next((tag[8:] for tag in tags if tag.startswith("license:")), None)
        pipeline = item.get("pipeline_tag")
        text = " ".join([repo_id, query, str(pipeline or ""), " ".join(tags)]).lower()
        score = sum(weight for keyword, weight in _RELEVANCE.items() if keyword in text)
        downloads = item.get("downloads", 0)
        likes = item.get("likes", 0)
        return {
            "repo_id": repo_id, "kind": kind,
            "url": f"https://huggingface.co/{kind}/{repo_id}" if kind != "models" else f"https://huggingface.co/{repo_id}",
            "last_modified": item.get("lastModified") or item.get("last_modified"),
            "downloads": downloads if isinstance(downloads, int) and downloads >= 0 else 0,
            "likes": likes if isinstance(likes, int) and likes >= 0 else 0,
            "tags": tags, "pipeline_tag": pipeline if isinstance(pipeline, str) else None,
            "license": license_name if isinstance(license_name, str) else None,
            "gated": bool(item.get("gated", False)), "relevance_score": score,
            "review_status": "needs_license_and_security_review" if not license_name else "needs_security_and_compatibility_review",
            "query_matches": (query,),
        }

    def search(self, *, kinds=("models", "datasets", "spaces"), queries=None, limit=10):
        """Return deduplicated candidates sorted by relevance, freshness and popularity."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        kinds = tuple(kinds)
        if not kinds or any(kind not in _DEFAULT_QUERIES for kind in kinds):
            raise ValueError("kinds must contain models, datasets, and/or spaces")
        if queries is None:
            query_map = {kind: _DEFAULT_QUERIES[kind] for kind in kinds}
        elif isinstance(queries, dict):
            query_map = {kind: tuple(queries.get(kind, ())) for kind in kinds}
        else:
            raise TypeError("queries must be a mapping from repository kind to query strings")
        candidates = {}
        for kind in kinds:
            for query in query_map[kind]:
                if not isinstance(query, str) or not query.strip() or len(query) > 80:
                    continue
                params = urlencode({"search": query.strip(), "sort": "lastModified", "direction": "-1", "limit": min(self.max_results_per_query, 100)})
                items = self._get_json(f"{_BASE}/{kind}?{params}")
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    candidate = self._metadata(kind, item, query.strip())
                    if not candidate:
                        continue
                    key = (kind, candidate["repo_id"])
                    previous = candidates.get(key)
                    if previous:
                        candidate["query_matches"] = tuple(sorted(set(previous["query_matches"] + candidate["query_matches"])))
                        candidate["relevance_score"] = max(previous["relevance_score"], candidate["relevance_score"]) + min(3, len(candidate["query_matches"]) - 1)
                        # Preserve the richest available metadata from either search result.
                        if not candidate["license"]: candidate["license"] = previous["license"]
                        if not candidate["tags"]: candidate["tags"] = previous["tags"]
                    candidates[key] = candidate
                time.sleep(0)  # Explicit yield point; no intentional polling loop.
        def sort_key(candidate):
            modified = candidate["last_modified"] or ""
            return (candidate["relevance_score"], modified, candidate["likes"], candidate["downloads"])
        ordered = sorted(candidates.values(), key=sort_key, reverse=True)[:limit]
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "source": "Hugging Face Hub public API",
                "automatic_installation": False, "automatic_execution": False,
                "results": ordered, "result_count": len(ordered),
                "notice": "Discovery is not a license, security, quality, or free-access guarantee. Review model cards, terms, dependencies, hardware needs, and code before adoption."}

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Discover recent Hugging Face assets relevant to Yuki")
    parser.add_argument("--kind", choices=("models", "datasets", "spaces", "all"), default="all")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--query", action="append", help="Search term; may be repeated")
    args = parser.parse_args()
    kinds = ("models", "datasets", "spaces") if args.kind == "all" else (args.kind,)
    queries = {kind: tuple(args.query or _DEFAULT_QUERIES[kind]) for kind in kinds}
    print(json.dumps(HuggingFaceScout().search(kinds=kinds, queries=queries, limit=args.limit), indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
