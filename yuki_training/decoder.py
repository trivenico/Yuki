"""Small decoder-only Transformer used for first-party Yuki-Core experiments.

PyTorch is an optional training dependency; importing the tokenizer does not
require it. This prototype is a training proof, not a production assistant.
"""
from __future__ import annotations

try:
    import torch
    from torch import nn
except ImportError as exc:  # pragma: no cover - depends on optional extra
    raise ImportError("Install Yuki training dependencies with: pip install -e '.[training]'") from exc


class YukiDecoder(nn.Module):
    def __init__(self, *, vocab_size: int = 256, block_size: int = 256, n_embd: int = 128,
                 n_head: int = 4, n_layer: int = 4, dropout: float = 0.1):
        super().__init__()
        if min(vocab_size, block_size, n_embd, n_head, n_layer) <= 0:
            raise ValueError("model dimensions must be positive")
        if n_embd % n_head:
            raise ValueError("n_embd must be divisible by n_head")
        if not 0.0 <= dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")
        self.block_size = block_size
        self.token_embedding = nn.Embedding(vocab_size, n_embd)
        self.position_embedding = nn.Embedding(block_size, n_embd)
        layer = nn.TransformerEncoderLayer(
            d_model=n_embd, nhead=n_head, dim_feedforward=4 * n_embd,
            dropout=dropout, batch_first=True, norm_first=True, activation="gelu",
        )
        self.blocks = nn.TransformerEncoder(layer, num_layers=n_layer, enable_nested_tensor=False)
        self.final_norm = nn.LayerNorm(n_embd)
        # Keep the output projection independent. Safetensors intentionally
        # rejects aliased tensors in a plain state_dict; an explicit untied
        # head keeps checkpoints portable and strict-loadable without custom
        # shared-weight serialization conventions.
        self.lm_head = nn.Linear(n_embd, vocab_size, bias=False)

    def forward(self, token_ids):
        if token_ids.ndim != 2:
            raise ValueError("token_ids must have shape [batch, sequence]")
        _, length = token_ids.shape
        if length > self.block_size:
            raise ValueError("sequence exceeds configured block_size")
        positions = torch.arange(length, device=token_ids.device)
        hidden = self.token_embedding(token_ids) + self.position_embedding(positions)[None, :, :]
        causal_mask = torch.triu(torch.ones(length, length, device=token_ids.device, dtype=torch.bool), diagonal=1)
        hidden = self.blocks(hidden, mask=causal_mask)
        return self.lm_head(self.final_norm(hidden))
