"""Pick a backend per pipeline stage from config presets; track cost; handle fallback.

Rule: a paid API is never used in place of the configured backend without the user
saying yes (`confirm` callback), and the estimated cost is shown when asking.
"""
from __future__ import annotations

from typing import Any, Callable

from cardcutter.config import Config
from cardcutter.llm.anthropic_backend import AnthropicBackend
from cardcutter.llm.base import BackendUnavailable, LLMBackend, LLMResult, Prompt
from cardcutter.llm.cost import CostTracker, estimate_tokens
from cardcutter.llm.env import load_dotenv
from cardcutter.llm.gemini import GeminiBackend
from cardcutter.llm.ollama import OllamaBackend
from cardcutter.llm.openai_compat import OpenAICompatBackend

BACKEND_TYPES: dict[str, type] = {
    "ollama": OllamaBackend,
    "openai_compat": OpenAICompatBackend,
    "anthropic": AnthropicBackend,
    "gemini": GeminiBackend,
}
STAGES = ("queries", "screen", "locate", "underline", "highlight", "trim", "emphasis", "tag", "readback", "intent",
          "quals", "embed")


class Registry:
    def __init__(self, cfg: Config, preset: str | None = None, overrides: dict[str, str] | None = None):
        self.cfg = cfg
        load_dotenv(cfg.root / ".env")
        self.preset = preset or cfg.get("llm.preset", "local")
        if self.preset not in (cfg.get("llm.presets") or {}):
            raise KeyError(f"unknown preset `{self.preset}`; options: {sorted(cfg.get('llm.presets') or {})}")
        self.overrides: dict[str, str] = dict(overrides or {})
        self._built: dict[str, LLMBackend] = {}
        self.costs = CostTracker.from_config(cfg)

    # -- resolution -----------------------------------------------------------------
    def backend_name(self, stage: str) -> str:
        if stage in self.overrides:
            return self.overrides[stage]
        p = self.cfg.get(f"llm.presets.{self.preset}") or {}
        name = p.get(stage) or (p.get("default") if stage != "embed" else None)
        if not name:
            raise KeyError(f"preset `{self.preset}` sets no backend for stage `{stage}`")
        if name == "off":  # a preset can switch an optional stage off: trim = "off"
            raise KeyError(f"stage `{stage}` is off in preset `{self.preset}`")
        return name

    def has_stage(self, stage: str) -> bool:
        try:
            self.backend_name(stage)
            return True
        except KeyError:
            return False

    def backend(self, name: str) -> LLMBackend:
        if name not in self._built:
            spec: dict[str, Any] = dict(self.cfg.get(f"llm.backends.{name}") or {})
            if not spec:
                raise KeyError(f"no [llm.backends.{name}] in config.toml")
            kind = spec.pop("type")
            if kind not in BACKEND_TYPES:
                raise KeyError(f"backend `{name}` has unknown type `{kind}`")
            self._built[name] = BACKEND_TYPES[kind](name=name, **spec)
        return self._built[name]

    def for_stage(self, stage: str) -> LLMBackend:
        return self.backend(self.backend_name(stage))

    def describe(self, stages: tuple[str, ...] = STAGES) -> dict[str, str]:
        out = {}
        for s in stages:
            try:
                b = self.for_stage(s)
                out[s] = f"{b.name} ({b.model}{', local' if b.is_local else ', free tier' if b.is_free else ', paid'})"
            except KeyError:
                continue
        return out

    # -- availability and fallback ----------------------------------------------------
    def ensure_available(self, stages: tuple[str, ...], confirm: Callable[[str], bool]) -> list[str]:
        """Check each backend the stages use. If a local one is down, offer the fallback."""
        notes: list[str] = []
        for name in sorted({self.backend_name(s) for s in stages}):
            b = self.backend(name)
            try:
                b.check()
                continue
            except BackendUnavailable as e:
                problem = str(e)
            fb_name = self.cfg.get("llm.fallback")
            if not b.is_local or not fb_name or fb_name == name:
                raise BackendUnavailable(problem)
            fb = self.backend(fb_name)
            fb.check()  # raises if the fallback has no key either
            kind = "free tier" if fb.is_free else "PAID API"
            est = self.estimate_card_cost(fb)
            question = (f"{problem}\nFall back to `{fb.name}` ({fb.model}, {kind}"
                        + (f", about ${est:.3f} per card" if not fb.is_free else "") + ") for this run?")
            if not confirm(question):
                raise BackendUnavailable(problem)
            for s in stages:
                if self.backend_name(s) == name:
                    self.overrides[s] = fb_name
            notes.append(f"`{name}` unavailable; using `{fb_name}` ({kind}) instead.")
        return notes

    # -- calling with cost accounting ---------------------------------------------------
    def est_output_tokens(self, stage: str) -> int:
        return int((self.cfg.get("llm.est_output_tokens") or {}).get(stage, 800))

    def estimate_call(self, stage: str, prompt: Prompt, backend: LLMBackend | None = None) -> float:
        b = backend or self.for_stage(stage)
        return self.costs.cost(b.model, estimate_tokens(prompt.system + prompt.user), self.est_output_tokens(stage), free=b.is_free)

    def estimate_card_cost(self, backend: LLMBackend, words: int = 400) -> float:
        """Rough cost of the cut stages for one card of `words` words."""
        prompt_tokens = 900 + int(words * 3.2)  # instructions + index:word view
        total = 0.0
        for stage in ("underline", "highlight", "emphasis"):
            total += self.costs.cost(backend.model, prompt_tokens, self.est_output_tokens(stage), free=backend.is_free)
        return total

    def call(self, stage: str, prompt: Prompt, schema: dict[str, Any], schema_name: str = "result") -> LLMResult:
        b = self.for_stage(stage)
        if not b.is_free:
            self.costs.guard(self.estimate_call(stage, prompt, b))
        res = b.generate_json(prompt, schema, schema_name=schema_name)
        res.cost_usd = self.costs.cost(b.model, res.input_tokens, res.output_tokens, free=b.is_free)
        self.costs.record(res.cost_usd, res.input_tokens, res.output_tokens)
        return res
