"""The one interface every model backend implements."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import jsonschema


class BackendError(Exception):
    """A backend call failed (HTTP error, refusal, bad output after retries)."""


class BackendUnavailable(BackendError):
    """The backend can't be reached at all (local server not running, no API key)."""


@dataclass
class Prompt:
    system: str
    user: str


@dataclass
class LLMResult:
    data: Any                      # parsed JSON
    text: str                      # raw text the model returned
    backend: str                   # config name, e.g. "ollama"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0         # includes thinking tokens where the provider bills them
    thinking_tokens: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0
    attempts: int = 1
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMBackend(Protocol):
    name: str
    model: str
    is_local: bool
    is_free: bool

    def generate_json(self, prompt: Prompt, schema: dict[str, Any], *, schema_name: str = "result") -> LLMResult: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def check(self) -> None:
        """Raise BackendUnavailable with a clear message if this backend can't be used."""
        ...


def parse_json(text: str, schema: dict[str, Any]) -> Any:
    """Parse and validate model output. Tolerates a ```json fence; nothing else."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    data = json.loads(t)
    jsonschema.validate(data, schema)
    return data
