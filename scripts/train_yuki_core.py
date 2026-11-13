#!/usr/bin/env python3
"""Train a tiny Yuki-Core decoder from random initialization.

Example:
  python scripts/train_yuki_core.py --corpus data/approved_corpus.txt \
    --output artifacts/yuki-core/0.1.0 --license "CC-BY-4.0" \
    --training-data-description "Reviewed corpus snapshot sha256:..." --steps 1000

Only use a corpus whose licenses and permitted uses have been reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--license", required=True, help="Reviewed license identifier or documented rights basis")
    parser.add_argument("--training-data-description", required=True, help="Dataset manifest ID and provenance summary")
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--block-size", type=int, default=128)
    parser.add_argument("--n-embd", type=int, default=128)
    parser.add_argument("--n-head", type=int, default=4)
    parser.add_argument("--n-layer", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.steps < 1 or args.batch_size < 1 or args.block_size < 2 or args.learning_rate <= 0:
        raise SystemExit("steps, batch-size, learning-rate must be positive and block-size >= 2")
    if not args.license.strip() or not args.training_data_description.strip():
        raise SystemExit("license and training-data-description must be non-empty")

    try:
        import torch
        import torch.nn.functional as F
    except ImportError as exc:
        raise SystemExit("PyTorch is optional. Install it with: pip install -e '.[training]'") from exc

    from yuki_training.byte_tokenizer import ByteTokenizer
    from yuki_training.decoder import YukiDecoder

    corpus_path = args.corpus.expanduser().resolve(strict=True)
    if not corpus_path.is_file():
        raise SystemExit("corpus must be a regular file")
    raw = corpus_path.read_bytes()
    if len(raw) < args.block_size + 2:
        raise SystemExit("corpus is too small for the selected block-size")
    text = raw.decode("utf-8")
    token_ids = torch.tensor(list(text.encode("utf-8")), dtype=torch.long)
    split = max(args.block_size + 1, int(len(token_ids) * 0.9))
    split = min(split, len(token_ids) - 1)
    train_data, val_data = token_ids[:split], token_ids[split:]
    if len(val_data) < args.block_size + 1:
        val_data = train_data

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    device = "cuda" if args.device == "auto" and torch.cuda.is_available() else args.device
    if device == "auto":
        device = "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA was requested but is unavailable")

    model_config = {"vocab_size": 256, "block_size": args.block_size, "n_embd": args.n_embd,
                    "n_head": args.n_head, "n_layer": args.n_layer, "dropout": 0.1}
    model = YukiDecoder(**model_config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)

    def batch(source):
        starts = torch.randint(0, len(source) - args.block_size - 1, (args.batch_size,))
        x = torch.stack([source[int(i):int(i) + args.block_size] for i in starts]).to(device)
        y = torch.stack([source[int(i) + 1:int(i) + args.block_size + 1] for i in starts]).to(device)
        return x, y

    model.train()
    for step in range(1, args.steps + 1):
        x, y = batch(train_data)
        logits = model(x)
        loss = F.cross_entropy(logits.reshape(-1, 256), y.reshape(-1))
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        if step == 1 or step % max(1, args.steps // 10) == 0 or step == args.steps:
            print(f"step={step}/{args.steps} train_loss={loss.item():.4f}", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    weights_path = args.output / "weights.pt"
    torch.save({"state_dict": model.cpu().state_dict(), "model_config": model_config, "seed": args.seed}, weights_path)
    tokenizer_path = args.output / "tokenizer.json"
    tokenizer_path.write_text(json.dumps(ByteTokenizer().to_manifest(), indent=2) + "\n", encoding="utf-8")
    config_path = args.output / "config.json"
    config_path.write_text(json.dumps({**model_config, "model_id": "yuki-core", "version": "0.1.0"}, indent=2) + "\n", encoding="utf-8")
    artifacts = {}
    for key, path in (("weights", weights_path), ("tokenizer", tokenizer_path), ("config", config_path)):
        artifacts[key] = {"path": path.name, "sha256": sha256(path)}
    manifest = {
        "schema_version": 1, "model_id": "yuki-core", "version": "0.1.0", "family": "language",
        "architecture": "yuki_decoder_v1", "origin": "random_init", "license": args.license,
        "training_data": args.training_data_description,
        "tasks": ["next_token_prediction", "language_model_pretraining"], "artifacts": artifacts,
    }
    manifest_path = args.output / "yuki-core.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"artifact_manifest={manifest_path}")
    print(f"corpus_sha256={sha256(corpus_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
