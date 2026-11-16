"""Anthropic Messages API via the official SDK. Structured output via output_config.format."""
from __future__ import annotations

from typing import Any

import anthropic

from cardcutter.llm.base import BackendError, BackendUnavailable, LLMResult, Prompt
from cardcutter.llm.env import get_key, redact
from cardcutter.llm.retry import generate_with_retries

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept server-side refusal fallbacks (Haiku has none).
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5")


class AnthropicBackend:
    is_local = False
    is_free = False

    def __init__(
        self,
        name: str,
        model: str,
        effort: str | None = "low",
        api_key_env: str = "ANTHROPIC_API_KEY",
        base_url: str = "https://api.anthropic.com",
        max_tokens: int = 16000,
        fallbacks: str | None = "default",
        timeout: float = 600.0,
        max_attempts: int = 2,
    ):
        self.name, self.model, self.effort = name, model, effort
        self.api_key_env, self.base_url, self.max_tokens = api_key_env, base_url, max_tokens
        self.fallbacks = fallbacks if model in FALLBACK_MODELS else None
        self.timeout, self.max_attempts = timeout, max_attempts
        self._client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            key = get_key(self.api_key_env)
            if not key:
                raise BackendUnavailable(f"`{self.name}` needs {self.api_key_env} (environment or .env)")
            # base_url is passed explicitly so a stray ANTHROPIC_BASE_URL in the shell is not used.
            self._client = anthropic.Anthropic(api_key=key, base_url=self.base_url, timeout=self.timeout, max_retries=3)
        return self._client

    def check(self) -> None:
        if not get_key(self.api_key_env):
            raise BackendUnavailable(f"`{self.name}` needs {self.api_key_env} (environment or .env)")

    def _call(self, prompt: Prompt, history: list[dict[str, str]], schema: dict[str, Any]):
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=prompt.system,
            messages=[{"role": "user", "content": prompt.user}, *history],
            output_config=output_config,
        )
        try:
            if self.fallbacks:
                resp = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks=self.fallbacks, **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise BackendUnavailable(f"{self.api_key_env} was rejected (401). Check the key.") from e
        except anthropic.RateLimitError as e:
            raise BackendError("Anthropic rate limit hit after SDK retries; try again later.") from e
        except anthropic.APIConnectionError as e:
            raise BackendUnavailable(f"Can't reach {self.base_url}: {e}") from e
        except anthropic.APIStatusError as e:
            raise BackendError(redact(f"Anthropic HTTP {e.status_code}: {e.message}")) from e

        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise BackendError(f"Anthropic declined the request (refusal, category={cat}).")
        if resp.stop_reason == "max_tokens":
            raise BackendError(f"Anthropic hit max_tokens={self.max_tokens}; raise it in config.toml.")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        u = resp.usage
        usage = {"input": (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
                 + (getattr(u, "cache_creation_input_tokens", 0) or 0),
                 "output": u.output_tokens or 0, "thinking": 0}
        return text, usage, {"served_by": resp.model, "stop_reason": resp.stop_reason}

    def generate_json(self, prompt: Prompt, schema: dict[str, Any], *, schema_name: str = "result") -> LLMResult:
        return generate_with_retries(
            lambda p, h: self._call(p, h, schema), prompt, schema,
            backend=self.name, model=self.model, max_attempts=self.max_attempts,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise BackendError("Anthropic has no embeddings endpoint; set an `embed` backend in the preset.")
