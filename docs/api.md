# Yuki Provider API Contract

AI providers in Yuki implement a simple, provider-independent contract.

## Base Provider Interface

```python
class BaseProvider:
    def is_available(self) -> bool:
        """Return True if the provider and model are reachable."""
        raise NotImplementedError

    def generate(self, prompt: str) -> str:
        """Single-shot generation prompt for task agent planning."""
        raise NotImplementedError

    def chat(self, messages: list[dict]) -> str:
        """Multi-turn conversational chat with system and user messages."""
        raise NotImplementedError
```
