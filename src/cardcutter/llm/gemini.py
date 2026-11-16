"""Google Gemini API. Structured output via the Interactions endpoint's response_format
(JSON schema enforced by constrained decoding). Embeddings via batchEmbedContents."""
from __future__ import annotations

import time
from typing import Any

import httpx

from cardcutter.llm.base import BackendError, BackendUnavailable, LLMResult, Prompt
from cardcutter.llm.env import get_key, redact
from cardcutter.llm.retry import generate_with_retries

API = "https://generativelanguage.googleapis.com/v1beta"
API_REVISION = "2026-05-20"


class GeminiBackend:
    is_local = False

    def __init__(
        self,
        name: str,
        model: str,
        api_key_env: str = "GEMINI_API_KEY",
        thinking_level: str | None = "low",
        max_output_tokens: int = 8192,
        is_free: bool = False,
        rpm: int | None = None,
        timeout: float = 600.0,
        max_attempts: int = 2,
    ):
        self.name, self.model, self.api_key_env = name, model, api_key_env
        self.thinking_level, self.max_output_tokens = thinking_level, max_output_tokens
        self.is_free = is_free
        self.min_interval = 60.0 / rpm if rpm else 0.0
        self._last = 0.0
        self.max_attempts = max_attempts
        self.http = httpx.Client(timeout=timeout)

    def check(self) -> None:
        if not get_key(self.api_key_env):
            raise BackendUnavailable(f"`{self.name}` needs {self.api_key_env} (environment or .env)")

    def _headers(self, interactions: bool = True) -> dict[str, str]:
        key = get_key(self.api_key_env)
        if not key:
            raise BackendUnavailable(f"`{self.name}` needs {self.api_key_env} (environment or .env)")
        h = {"x-goog-api-key": key, "Content-Type": "application/json"}
        if interactions:
            h["Api-Revision"] = API_REVISION
        return h

    def _post(self, url: str, body: dict[str, Any], interactions: bool = True) -> dict[str, Any]:
        r = None
        for attempt in range(6):
            wait = self._last + self.min_interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.http.post(url, json=body, headers=self._headers(interactions))
            except httpx.ConnectError as e:
                raise BackendUnavailable(f"Can't reach Gemini API: {e}") from e
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(float(r.headers.get("retry-after", min(60, 5 * 2 ** attempt))))
                continue
            if r.status_code >= 400:
                raise BackendError(redact(f"Gemini HTTP {r.status_code}: {r.text[:400]}"))
            return r.json()
        raise BackendError(f"Gemini still failing after retries (HTTP {r.status_code if r else '?'}); "
                           "free-tier daily limits may be used up.")

    def _call(self, prompt: Prompt, history: list[dict[str, str]], schema: dict[str, Any]):
        user = prompt.user
        if history:  # one-shot API: fold the retry feedback into the input
            user += "\n\n" + "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in history)
        gen: dict[str, Any] = {"max_output_tokens": self.max_output_tokens}
        if self.thinking_level:
            gen["thinking_level"] = self.thinking_level
        body = {
            "model": self.model,
            "input": user,
            "system_instruction": prompt.system,
            "generation_config": gen,
            "response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
        }
        resp = self._post(f"{API}/interactions", body)
        text = "".join(
            part.get("text", "")
            for step in resp.get("steps", []) if step.get("type") == "model_output"
            for part in step.get("content", []) if part.get("type") == "text"
        )
        u = resp.get("usage") or {}
        thought = u.get("total_thought_tokens", 0) or 0
        usage = {"input": u.get("total_input_tokens", 0) or 0,
                 "output": (u.get("total_output_tokens", 0) or 0) + thought, "thinking": thought}
        return text, usage, {"status": resp.get("status")}

    def generate_json(self, prompt: Prompt, schema: dict[str, Any], *, schema_name: str = "result") -> LLMResult:
        return generate_with_retries(
            lambda p, h: self._call(p, h, schema), prompt, schema,
            backend=self.name, model=self.model, max_attempts=self.max_attempts,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):
            chunk = texts[i:i + 100]
            body = {"requests": [{"model": f"models/{self.model}", "content": {"parts": [{"text": t}]}} for t in chunk]}
            resp = self._post(f"{API}/models/{self.model}:batchEmbedContents", body, interactions=False)
            out.extend(e["values"] for e in resp["embeddings"])
        return out
