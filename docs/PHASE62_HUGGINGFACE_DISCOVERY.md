# Phase 62 — Hugging Face model discovery

Yuki's discovery feature searches public Hugging Face Hub metadata only. It never downloads model weights, installs dependencies, executes repository code, or changes the active provider. Candidate models require human review of the model card, license, security posture, hardware requirements, context window, and task-specific evaluation before use.

Search source: https://huggingface.co/models

Run the discovery command after implementation with `python -m providers.huggingface_discovery --query "instruction model" --task text-generation --limit 10`.

Discovery results are not endorsements or benchmark scores. Missing license metadata is a review blocker; gated models require separate access review. Credentials, if configured, are sent only to the official Hugging Face Hub API endpoint. The model catalog should not auto-enable or auto-download new models.