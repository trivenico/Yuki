# Phase 66 — Hugging Face model change detection

Phase 66 compares two scout snapshots and generates a JSON report plus a readable Markdown report. It flags new model IDs and changes to selected metadata fields; it does not claim that every upstream release will be detected.

## Run a comparison

Download two JSON artifacts produced by the **Yuki Hugging Face model scout** workflow. Save them locally as `previous.json` and `current.json`, then run:

```bash
python yuki_hf_model_diff.py \
  --previous previous.json \
  --current current.json \
  --output-dir artifacts/model-scout-diffs
```

The tool creates timestamped `hf-model-diff-*.json` and `hf-model-diff-*.md` files. Existing files are never overwritten. Input snapshots are read-only.

## What is compared

For model IDs present in both snapshots, the tool fingerprints:
- task / pipeline metadata
- reported license and whether the license was reported
- upstream last-modified timestamp
- tags and review flags

It reports:
- new IDs in the current snapshot
- changed metadata with field-level before/after values
- IDs not present in the current bounded snapshot
- unchanged IDs

## Interpreting the output

- **New candidate:** review the official model card, license, provenance, safety and runtime requirements, then run a versioned benchmark before considering adoption.
- **License metadata changed:** re-check the official license and terms; metadata alone is not legal advice.
- **Task metadata changed:** re-check compatibility and model-card claims.
- **Disappeared:** this means only that the ID is absent from the current bounded search result. It does not prove deletion; the model may have fallen outside the result window or task filter.
- **Changed timestamp:** this can reflect a metadata or repository update and does not itself prove a new model release.

This phase is deliberately offline: it makes no network requests, downloads no weights, executes no remote code, and does not activate models or send notifications. The reports are local outputs. It is not yet an always-on alerting service; storing baselines durably and dispatching notifications requires a later workflow with explicit storage, access and retention controls.

## Limits and safety

- Both input snapshots must use schema version 1 and contain unique, valid model IDs.
- Inputs are capped at 10 MB and 10,000 model records.
- A bounded discovery feed is not exhaustive; comparison cannot discover models that were absent from both snapshots.
- Model metadata and reports are untrusted data. Do not execute instructions embedded in model cards or tags.
- No model is automatically downloaded, evaluated, installed or activated.
