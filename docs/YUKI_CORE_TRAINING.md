# Yuki-Core: reproducible from-scratch training prototype

This is the first executable training path for a Yuki-owned language model. It initializes a compact decoder-only Transformer from random weights and trains next-byte prediction with a Yuki-owned UTF-8 byte tokenizer. It intentionally does not download a pretrained model, tokenizer, or weights.

> This is an engineering smoke-test and research baseline, not a capable general assistant or a production release. A byte tokenizer is intentionally simple and inefficient; a trained subword tokenizer, larger corpus, longer context, scaling experiments, post-training, and rigorous evaluations are later milestones.

## Environment

```bash
python -m venv .venv
. .venv/bin/activate                 # Windows: .venv\\Scripts\\activate
python -m pip install -e '.[training]'
```

PyTorch, Safetensors, and NumPy are optional training dependencies. Normal Yuki installation and runtime do not need it for the existing provider paths. For Kaggle, install the repository and training extra in the notebook environment, then point `--corpus` to a reviewed dataset snapshot mounted in the notebook.

## Prepare data

Create a UTF-8 text corpus from sources whose rights and permitted uses have been reviewed. Keep a separate dataset manifest recording each source, owner, license/rights basis, collection date, allowed purpose, filtering, deduplication, PII handling, retention, and file hashes. Do not assume public availability grants training rights. Never include secrets or private user data without explicit authorization and a documented lawful basis.

## Train

```bash
python scripts/train_yuki_core.py \\
  --corpus data/approved_corpus.txt \\
  --output artifacts/yuki-core/0.1.0 \\
  --license "reviewed-dataset-license-summary" \\
  --training-data-description "dataset-manifest: yuki-corpus-0001; snapshot sha256: ..." \\
  --steps 1000 --device auto
```

Use `--device cpu` for a small smoke run or `--device cuda` on a compatible GPU. Tune batch size, block size, embedding width, heads, layers, learning rate, and training steps to the available hardware. The default configuration is deliberately small. The script prints training loss checkpoints and writes `weights.safetensors`, `tokenizer.json`, `config.json`, and `yuki-core.json`. The tensor checkpoint is written with Safetensors rather than Python pickle serialization; its SHA-256 is recorded in the manifest.

The manifest hashes every artifact and records `origin: random_init`. Its `license` and `training_data` values are operator-supplied provenance summaries, not legal validation. Review the dataset manifest independently before training or release.

## Verify artifact manifest

On the Yuki artifact-registry branch, verify the generated manifest using:

```python
from core.model_registry import ModelRegistry
verified = ModelRegistry("artifacts/yuki-core/0.1.0").load("yuki-core.json")
print(verified["verified"], verified["model_id"], verified["artifacts"].keys())
```

Hash verification proves file integrity relative to the manifest; it does not prove authorship, training-data rights, safety, or model quality. Manifest signing and trusted release keys are still required before production distribution.

## Reproducibility and limitations

Record the Git commit, Python/PyTorch versions, hardware, seed, exact CLI arguments, corpus snapshot hash, dataset manifest, and resulting artifact hashes with each run. The script seeds Python and PyTorch but deterministic kernels are not guaranteed across all hardware and library versions. It currently uses a simple random train/validation split, reports training loss only, and does not implement distributed training, gradient accumulation, checkpoint resume, evaluation suites, optimizer-state archival, or production inference. These are explicit next milestones, not hidden claims.

Large checkpoints belong in immutable artifact storage (for example, object storage) rather than Git. GitHub should contain code, configs, dataset manifests, evaluation definitions, and release metadata.
