"""Any OpenAI-compatible chat API: LM Studio, mlx_lm.server, llama.cpp llama-server,
OpenAI, OpenRouter, Groq, ... Structured output via response_format json_schema."""
from __future__ import annotations

import time
from typing import Any

import httpx

from cardcutter.llm.base import BackendError, BackendUnavailable, LLMResult, Prompt
from cardcutter.llm.env import get_key, redact
from cardcutter.llm.retry import generate_with_retries


class OpenAICompatBackend:
    def __init__(
        self,
        name: str,
        model: str,
        base_url: str,
        api_key_env: str | None = None,
        is_local: bool = False,
        is_free: bool = False,
        temperature: float | None = 0.0,
        max_tokens: int = 4096,
        structured: str = "json_schema",  # json_schema | json_object | none (prompt only)
        extra_body: dict[str, Any] | None = None,
        rpm: int | None = None,
        timeout: float = 900.0,
        max_attempts: int = 2,
    ):
        self.name, self.model, self.base_url = name, model, base_url.rstrip("/")
        self.api_key_env, self.is_local, self.is_free = api_key_env, is_local, is_free
        self.temperature, self.max_tokens, self.structured = temperature, max_tokens, structured
        self.extra_body = extra_body or {}
        self.min_interval = 60.0 / rpm if rpm else 0.0
        self._last = 0.0
        self.max_attempts = max_attempts
        self.http = httpx.Client(timeout=timeout)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key_env:
            key = get_key(self.api_key_env)
            if key:
                h["Authorization"] = f"Bearer {key}"
        return h

    def check(self) -> None:
        if not self.model:
            raise BackendUnavailable(f"backend `{self.name}` has no model set in config.toml")
        if self.api_key_env and not get_key(self.api_key_env):
            raise BackendUnavailable(f"`{self.name}` needs {self.api_key_env} (environment or .env)")
        if self.is_local:
            try:
                self.http.get(f"{self.base_url}/models", headers=self._headers(), timeout=5).raise_for_status()
            except httpx.HTTPError as e:
                raise BackendUnavailable(f"No OpenAI-compatible server at {self.base_url} ({type(e).__name__}).") from e

    def _throttle(self) -> None:
        wait = self._last + self.min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(5):
            self._throttle()
            try:
                r = self.http.post(f"{self.base_url}{path}", json=body, headers=self._headers())
            except httpx.ConnectError as e:
                raise BackendUnavailable(f"Can't reach {self.base_url}: {e}") from e
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(float(r.headers.get("retry-after", 2 ** attempt * 2)))
                continue
            if r.status_code >= 400:
                raise BackendError(redact(f"{self.name} HTTP {r.status_code}: {r.text[:300]}"))
            return r.json()
        raise BackendError(f"{self.name}: still failing after retries (HTTP {r.status_code})")

    def _call(self, prompt: Prompt, history: list[dict[str, str]], schema: dict[str, Any], schema_name: str):
        system = prompt.system
        if self.structured == "none":
            import json
            system += "\nReply with JSON matching this schema:\n" + json.dumps(schema)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt.user}, *history],
            **self.extra_body,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.structured == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": True}}
        elif self.structured == "json_object":
            body["response_format"] = {"type": "json_object"}
        resp = self._post("/chat/completions", body)
        choice = (resp.get("choices") or [{}])[0]
        u = resp.get("usage") or {}
        details = u.get("completion_tokens_details") or {}
        usage = {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0),
                 "thinking": details.get("reasoning_tokens", 0) or 0}
        return (choice.get("message") or {}).get("content") or "", usage, {"finish_reason": choice.get("finish_reason")}

    def generate_json(self, prompt: Prompt, schema: dict[str, Any], *, schema_name: str = "result") -> LLMResult:
        return generate_with_retries(
            lambda p, h: self._call(p, h, schema, schema_name), prompt, schema,
            backend=self.name, model=self.model, max_attempts=self.max_attempts,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._post("/embeddings", {"model": self.model, "input": texts})
        return [d["embedding"] for d in sorted(resp["data"], key=lambda d: d.get("index", 0))]
