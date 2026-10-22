# Hugging Face Scout (Phase 42)

Yuki can query the public Hugging Face Hub API for recent models, datasets and Spaces using `integrations/huggingface_scout.py`. It uses Python's standard library, bounded HTTP timeouts and response sizes, fixed HTTPS Hub API hosts, result limits, and metadata-based ranking. No Hub token is required for public repository metadata in ordinary use.

## Run

```sh
python -m integrations.huggingface_scout --kind all --limit 10
python -m integrations.huggingface_scout --kind models --query agent --query coding --limit 20
python -m unittest tests.test_huggingface_scout -v
```

## What it does

- Searches models, datasets and Spaces using Yuki-relevant terms (agents, coding, reasoning, embeddings, evaluation, vision and speech).
- Deduplicates repositories found by multiple queries and ranks candidates by relevance, last-modified timestamp, likes and downloads.
- Emits repository URLs, tags, task metadata, update time, popularity signals and any discoverable license identifier.
- Labels candidates as requiring license/security/compatibility review.

## What it does not do

- It does not install packages or model weights, run repository code, load pickles, execute Spaces, or grant tools to agents.
- A missing license field is not proof that an asset is open source. A visible license tag is not a substitute for reviewing the actual license and model card.
- “Free to download” does not mean free inference, commercial use, or low hardware requirements. Gated repositories may require approval or credentials.
- Popularity is not a quality or security guarantee. Before adoption, pin a revision, inspect model card and license, scan dependencies/weights, check provenance, measure memory/latency, and run an evaluation suite in an isolated environment.
- This is pull-based discovery, not a push alert service. A separate scheduled monitor can run it regularly; Hugging Face webhooks can provide event-driven updates for selected repositories once a public HTTPS receiver and secret are configured.

## Next steps

Integrate the scout into a scheduled discovery job with persisted seen-repository IDs, release-diff detection, relevance thresholds, and user-visible reports. Add evaluation profiles for Yuki's coding, reasoning, agent/tool-use, embedding, reranking and multimodal tasks. Never auto-promote a discovered model into production routing without explicit approval and successful evals.
