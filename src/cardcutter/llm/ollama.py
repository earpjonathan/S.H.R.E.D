"""Ollama native API. Structured output via `format` = JSON schema (grammar-enforced)."""
from __future__ import annotations

import time
from typing import Any

import httpx

from cardcutter.llm.base import BackendError, BackendUnavailable, LLMResult, Prompt
from cardcutter.llm.retry import generate_with_retries


class OllamaBackend:
    is_local = True
    is_free = True

    def __init__(
        self,
        name: str,
        model: str,
        base_url: str = "http://localhost:11434",
        think: bool | str = False,
        num_ctx: int = 16384,
        temperature: float = 0.0,
        num_predict: int = 2048,
        keep_alive: str = "30m",
        timeout: float = 1800.0,
        max_attempts: int = 2,
    ):
        self.name, self.model, self.base_url = name, model, base_url.rstrip("/")
        self.think, self.num_ctx, self.temperature = think, num_ctx, temperature
        self.num_predict, self.keep_alive, self.max_attempts = num_predict, keep_alive, max_attempts
        self.http = httpx.Client(timeout=timeout)

    def check(self) -> None:
        try:
            r = self.http.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendUnavailable(
                f"Ollama isn't running at {self.base_url} ({type(e).__name__}). Start it with `ollama serve` "
                "(or `brew services start ollama`)."
            ) from e
        names = {m.get("name") for m in r.json().get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
            raise BackendUnavailable(f"Ollama has no model `{self.model}`. Run: ollama pull {self.model}")

    def _call(self, prompt: Prompt, history: list[dict[str, str]], schema: dict[str, Any]):
        body = {
            "model": self.model,
            "stream": False,
            "think": self.think,
            "format": schema,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict * (4 if self.think else 1),
            },
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
                *history,
            ],
        }
        try:
            r = self.http.post(f"{self.base_url}/api/chat", json=body)
        except httpx.ConnectError as e:
            raise BackendUnavailable(f"Ollama isn't running at {self.base_url}. Start it with `ollama serve`.") from e
        if r.status_code == 501 and "structured output" in r.text:
            raise BackendError(
                f"Ollama can't enforce JSON schemas for `{self.model}` (an MLX build without xgrammar). "
                "Use a GGUF tag such as `qwen3.8:27b-q4_K_M`."
            )
        if r.status_code >= 400:
            raise BackendError(f"Ollama HTTP {r.status_code}: {r.text[:300]}")
        resp = r.json()
        msg = resp.get("message", {})
        usage = {"input": resp.get("prompt_eval_count", 0), "output": resp.get("eval_count", 0),
                 "thinking": 0}
        extra = {
            "prefill_tok_s": _rate(resp.get("prompt_eval_count", 0), resp.get("prompt_eval_duration", 0)),
            "gen_tok_s": _rate(resp.get("eval_count", 0), resp.get("eval_duration", 0)),
            "done_reason": resp.get("done_reason"),
        }
        return msg.get("content", ""), usage, extra

    def generate_json(self, prompt: Prompt, schema: dict[str, Any], *, schema_name: str = "result") -> LLMResult:
        return generate_with_retries(
            lambda p, h: self._call(p, h, schema), prompt, schema,
            backend=self.name, model=self.model, max_attempts=self.max_attempts,
        )

    # Ollama sometimes drops a large /api/embed request ("connection reset by peer" from its
    # runner's /tokenize), so texts go in small batches, and a failed batch is retried, then split.
    EMBED_BATCH = 32
    EMBED_CHARS = 8000

    def embed(self, texts: list[str]) -> list[list[float]]:
        texts = [t[:self.EMBED_CHARS] for t in texts]
        out: list[list[float]] = []
        for i in range(0, len(texts), self.EMBED_BATCH):
            out.extend(self._embed_retry(texts[i:i + self.EMBED_BATCH]))
        return out

    def _embed_retry(self, texts: list[str], tries: int = 3) -> list[list[float]]:
        err: BackendError | None = None
        for k in range(tries):
            try:
                return self._embed_once(texts)
            except BackendError as e:
                err = e
                time.sleep(1.0 + k)
        if len(texts) > 1:
            mid = len(texts) // 2
            return self._embed_retry(texts[:mid], 2) + self._embed_retry(texts[mid:], 2)
        assert err is not None
        raise err

    def _embed_once(self, texts: list[str]) -> list[list[float]]:
        try:
            r = self.http.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": texts, "keep_alive": self.keep_alive})
        except httpx.ConnectError as e:
            raise BackendUnavailable(f"Ollama isn't running at {self.base_url}. Start it with `ollama serve`.") from e
        except httpx.TransportError as e:
            raise BackendError(f"Ollama embed: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise BackendError(f"Ollama embed HTTP {r.status_code}: {r.text[:300]}")
        return r.json()["embeddings"]


def _rate(n: int, ns: int) -> float | None:
    return round(n / (ns / 1e9), 1) if n and ns else None
