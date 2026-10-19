"""Optional Apple MLX local inference adapter for Apple Silicon."""
from __future__ import annotations

class MLXProvider:
    def __init__(self, model_path: str, *, max_tokens: int = 2048, temperature: float = 0.2):
        if not isinstance(model_path, str) or not model_path.strip() or len(model_path) > 512:
            raise ValueError("An MLX model path or Hugging Face model ID is required.")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 8192:
            raise ValueError("max_tokens must be between 1 and 8192.")
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2.")
        try:
            from mlx_lm import generate, load
        except ImportError as exc:
            raise RuntimeError("Install optional Apple Silicon inference: pip install -r requirements-mlx.txt") from exc
        self._generate = generate
        self.model, self.tokenizer = load(model_path)
        self.model_name = model_path
        self.max_tokens = max_tokens
        self.temperature = float(temperature)
        self.last_usage = {}

    def is_available(self) -> bool:
        return self.model is not None and self.tokenizer is not None

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
            raise ValueError("Prompt must be non-empty and at most 20,000 characters.")
        result = self._generate(self.model, self.tokenizer, prompt=prompt,
                                max_tokens=self.max_tokens, verbose=False)
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("MLX returned no text.")
        # Current mlx_lm.generate API returns text only; report estimated usage in the wrapper.
        self.last_usage = {}
        return result.strip()
