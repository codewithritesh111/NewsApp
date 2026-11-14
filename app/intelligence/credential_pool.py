import json
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Generic, TypeVar

Credential = TypeVar("Credential")


class AIProviderError(RuntimeError):
    """Raised when an AI provider request or response fails."""


class RotatingCredentialPool(Generic[Credential]):
    def __init__(
        self,
        credentials: Iterable[Credential],
        provider_name: str,
        state_path: Path | None = None,
    ):
        self._credentials = tuple(credentials)
        if not self._credentials:
            raise ValueError(f"No {provider_name} credentials are configured.")
        self._provider_name = provider_name
        self._next_index = 0
        self._state_path = state_path
        self._lock = threading.Lock()

    def next_credential(self) -> Credential:
        with self._lock:
            if self._state_path is None:
                index = self._next_index
                self._next_index = (self._next_index + 1) % len(self._credentials)
                return self._credentials[index]

            state = self._read_state()
            provider_key = self._provider_name.casefold()
            index = state.get(provider_key, 0)
            if type(index) is not int or index < 0:
                raise ValueError(
                    f"Credential rotation index for {self._provider_name} "
                    "must be a non-negative integer."
                )
            credential_index = index % len(self._credentials)
            state[provider_key] = (credential_index + 1) % len(self._credentials)
            self._write_state(state)
            return self._credentials[credential_index]

    def attempts(self) -> Iterator[Credential]:
        for _ in range(len(self._credentials)):
            yield self.next_credential()

    def _read_state(self) -> dict[str, int]:
        if not self._state_path.exists():
            return {}
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Credential rotation state is invalid JSON: {self._state_path}"
            ) from exc
        if not isinstance(state, dict):
            raise ValueError(
                f"Credential rotation state must be a JSON object: {self._state_path}"
            )
        return state

    def _write_state(self, state: dict[str, int]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._state_path.with_suffix(
            f"{self._state_path.suffix}.{threading.get_ident()}.tmp"
        )
        temporary_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self._state_path)

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def credential_count(self) -> int:
        return len(self._credentials)

