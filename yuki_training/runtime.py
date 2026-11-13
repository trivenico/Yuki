"""Verified local inference for first-party Yuki-Core Safetensors releases.

Only the built-in, versioned YukiDecoder architecture is constructed. Artifact
files are never imported, unpickled, downloaded, or allowed to supply code.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class YukiCoreRuntimeError(ValueError):
    """Raised when a Yuki-Core release is invalid or cannot be loaded."""


_REQUIRED_MANIFEST = {
    "schema_version", "model_id", "version", "family", "architecture",
    "origin", "license", "training_data", "tasks", "artifacts",
}
_REQUIRED_ARTIFACTS = {"weights", "tokenizer", "config"}
_CONFIG_KEYS = {
    "vocab_size", "block_size", "n_embd", "n_head", "n_layer",
    "dropout", "model_id", "version",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained_file(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise YukiCoreRuntimeError(f"{label} path must be a non-empty relative path")
    try:
        path = (root / relative).resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as exc:
        raise YukiCoreRuntimeError(f"{label} path is missing or escapes the release directory") from exc
    if not path.is_file():
        raise YukiCoreRuntimeError(f"{label} must be a regular file")
    return path


def _read_json(path: Path, label: str, limit: int = 64 * 1024) -> dict:
    if path.stat().st_size > limit:
        raise YukiCoreRuntimeError(f"{label} exceeds the size limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YukiCoreRuntimeError(f"{label} must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise YukiCoreRuntimeError(f"{label} must be a JSON object")
    return value


class YukiCoreRuntime:
    """Load one verified Yuki-Core release and expose bounded greedy generation."""

    def __init__(self, release_dir: str | Path, *, device: str = "cpu"):
        root = Path(release_dir).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise YukiCoreRuntimeError("release_dir must be a directory")
        if device not in {"cpu", "cuda"}:
            raise YukiCoreRuntimeError("device must be cpu or cuda")

        manifest_path = _contained_file(root, "yuki-core.json", "manifest")
        manifest = _read_json(manifest_path, "manifest")
        if set(manifest) != _REQUIRED_MANIFEST:
            raise YukiCoreRuntimeError("manifest has missing or unsupported fields")
        if manifest["schema_version"] != 1 or isinstance(manifest["schema_version"], bool):
            raise YukiCoreRuntimeError("unsupported manifest schema")
        if manifest["model_id"] != "yuki-core" or manifest["architecture"] != "yuki_decoder_v1":
            raise YukiCoreRuntimeError("unsupported Yuki-Core model or architecture")
        if manifest["family"] != "language" or manifest["origin"] != "random_init":
            raise YukiCoreRuntimeError("this runtime accepts only Yuki-Core random-init releases")
        for field in ("version", "license", "training_data"):
            if not isinstance(manifest[field], str) or not manifest[field].strip() or len(manifest[field]) > 4000:
                raise YukiCoreRuntimeError(f"manifest {field} is invalid")
        tasks = manifest["tasks"]
        if (not isinstance(tasks, list) or not tasks or len(tasks) > 32
                or any(not isinstance(x, str) or not x.strip() or len(x) > 80 for x in tasks)
                or len(set(tasks)) != len(tasks)):
            raise YukiCoreRuntimeError("manifest tasks are invalid")
        descriptors = manifest["artifacts"]
        if not isinstance(descriptors, dict) or set(descriptors) != _REQUIRED_ARTIFACTS:
            raise YukiCoreRuntimeError("manifest artifacts are invalid")

        verified = {}
        for name, descriptor in descriptors.items():
            if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
                raise YukiCoreRuntimeError(f"{name} descriptor is invalid")
            expected = descriptor["sha256"]
            if (not isinstance(expected, str) or len(expected) != 64
                    or any(ch not in "0123456789abcdef" for ch in expected)):
                raise YukiCoreRuntimeError(f"{name} SHA-256 is invalid")
            path = _contained_file(root, descriptor["path"], name)
            max_bytes = 256 * 1024 * 1024 if name == "weights" else 64 * 1024
            if path.stat().st_size > max_bytes:
                raise YukiCoreRuntimeError(f"{name} artifact exceeds the size limit")
            if _sha256(path) != expected:
                raise YukiCoreRuntimeError(f"{name} integrity verification failed")
            verified[name] = path

        config = _read_json(verified["config"], "model config")
        if set(config) != _CONFIG_KEYS:
            raise YukiCoreRuntimeError("model config has missing or unsupported fields")
        if config["model_id"] != manifest["model_id"] or config["version"] != manifest["version"]:
            raise YukiCoreRuntimeError("model config identity does not match manifest")
        dimensions = ("vocab_size", "block_size", "n_embd", "n_head", "n_layer")
        for key in dimensions:
            value = config[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise YukiCoreRuntimeError(f"model config {key} must be a positive integer")
        if config["vocab_size"] != 256 or config["block_size"] > 8192:
            raise YukiCoreRuntimeError("unsupported tokenizer vocabulary or context size")
        if config["n_embd"] > 4096 or config["n_head"] > 64 or config["n_layer"] > 64:
            raise YukiCoreRuntimeError("model dimensions exceed runtime safety limits")
        if config["n_embd"] % config["n_head"]:
            raise YukiCoreRuntimeError("n_embd must be divisible by n_head")
        estimated_parameters = (
            2 * config["vocab_size"] * config["n_embd"]
            + config["block_size"] * config["n_embd"]
            + config["n_layer"] * (12 * config["n_embd"] ** 2 + 13 * config["n_embd"])
        )
        if estimated_parameters > 50_000_000:
            raise YukiCoreRuntimeError("model configuration exceeds the 50-million-parameter runtime limit")
        dropout = config["dropout"]
        if isinstance(dropout, bool) or not isinstance(dropout, (int, float)) or not 0 <= dropout < 1:
            raise YukiCoreRuntimeError("model dropout is invalid")

        tokenizer = _read_json(verified["tokenizer"], "tokenizer")
        if tokenizer != {
            "name": "yuki_utf8_byte_v1",
            "vocab_size": 256,
            "encoding": "UTF-8 bytes",
            "special_tokens": [],
        }:
            raise YukiCoreRuntimeError("unsupported Yuki-Core tokenizer")

        try:
            import torch
            from safetensors import safe_open
            from safetensors.torch import load_file
            from yuki_training.byte_tokenizer import ByteTokenizer
            from yuki_training.decoder import YukiDecoder
        except ImportError as exc:
            raise RuntimeError("Install Yuki training/runtime dependencies with: pip install -e '.[training]'") from exc
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable")

        try:
            with safe_open(str(verified["weights"]), framework="pt", device="cpu") as handle:
                metadata = handle.metadata() or {}
            if metadata.get("architecture") != manifest["architecture"]:
                raise YukiCoreRuntimeError("Safetensors architecture metadata mismatch")
            if metadata.get("model_id") != manifest["model_id"] or metadata.get("version") != manifest["version"]:
                raise YukiCoreRuntimeError("Safetensors identity metadata mismatch")
            state_dict = load_file(str(verified["weights"]), device="cpu")
            model = YukiDecoder(
                vocab_size=config["vocab_size"], block_size=config["block_size"],
                n_embd=config["n_embd"], n_head=config["n_head"],
                n_layer=config["n_layer"], dropout=float(config["dropout"]),
            )
            model.load_state_dict(state_dict, strict=True)
            model.to(device)
            model.eval()
        except YukiCoreRuntimeError:
            raise
        except Exception as exc:
            raise YukiCoreRuntimeError("Yuki-Core weights are incompatible with the trusted architecture") from exc

        self.root = root
        self.model_id = manifest["model_id"]
        self.version = manifest["version"]
        self.device = device
        self.block_size = config["block_size"]
        self._torch = torch
        self._model = model
        self._tokenizer = ByteTokenizer()

    def is_available(self) -> bool:
        """Return true when the verified model is loaded in this process."""
        return True

    def generate(self, prompt: str, *, max_new_tokens: int = 128) -> str:
        """Greedily generate at most max_new_tokens bytes; no tools or network calls."""
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if isinstance(max_new_tokens, bool) or not isinstance(max_new_tokens, int) or not 1 <= max_new_tokens <= 1024:
            raise ValueError("max_new_tokens must be between 1 and 1024")
        token_ids = self._tokenizer.encode(prompt)
        if not token_ids:
            raise ValueError("prompt must not be empty")
        generated = []
        with self._torch.inference_mode():
            for _ in range(max_new_tokens):
                context = token_ids[-self.block_size:]
                inputs = self._torch.tensor([context], dtype=self._torch.long, device=self.device)
                logits = self._model(inputs)[0, -1]
                next_token = int(self._torch.argmax(logits).item())
                token_ids.append(next_token)
                generated.append(next_token)
        return self._tokenizer.decode(generated)
