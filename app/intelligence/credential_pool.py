import threading
from collections.abc import Iterable, Iterator
from typing import Generic, TypeVar

Credential = TypeVar("Credential")


class AIProviderError(RuntimeError):
    """Raised when an AI provider request or response fails."""


class RotatingCredentialPool(Generic[Credential]):
    def __init__(self, credentials: Iterable[Credential], provider_name: str):
        self._credentials = tuple(credentials)
        if not self._credentials:
            raise ValueError(f"No {provider_name} credentials are configured.")
        self._provider_name = provider_name
        self._next_index = 0
        self._lock = threading.Lock()

    def attempts(self) -> Iterator[Credential]:
        with self._lock:
            start_index = self._next_index

        for offset in range(len(self._credentials)):
            index = (start_index + offset) % len(self._credentials)
            with self._lock:
                self._next_index = (index + 1) % len(self._credentials)
            yield self._credentials[index]

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def credential_count(self) -> int:
        return len(self._credentials)
