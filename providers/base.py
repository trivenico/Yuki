from abc import ABC, abstractmethod


class BaseProvider(ABC):
    """Common interface for every AI provider Yuki supports."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Generate a response from a prompt."""
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """Report whether this provider is ready to use."""
        raise NotImplementedError


    def close(self) -> None:
        """Release resources owned by this provider instance.

        Implementations with sockets, subprocesses, clients, or other persistent
        resources should override this method. Stateless providers may keep the
        default no-op implementation.
        """
        return None
