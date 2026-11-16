"""A scripted backend and a minimal config, so pipeline and eval tests make no model calls."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from cardcutter.config import Config
from cardcutter.llm.base import BackendUnavailable, LLMResult, Prompt
from cardcutter.llm.registry import Registry

Responder = Callable[[str, Prompt], Any]  # (stage guessed from schema) -> data


class FakeBackend:
    def __init__(self, name: str, responder: Responder, *, model: str = "fake-1", is_local: bool = True,
                 is_free: bool = True, available: bool = True):
        self.name, self.model, self.responder = name, model, responder
        self.is_local, self.is_free, self.available = is_local, is_free, available
        self.calls: list[tuple[str, Prompt]] = []

    def check(self) -> None:
        if not self.available:
            raise BackendUnavailable(f"{self.name} is down")

    def generate_json(self, prompt: Prompt, schema: dict[str, Any], *, schema_name: str = "result") -> LLMResult:
        self.calls.append((schema_name, prompt))
        data = self.responder(schema_name, prompt)
        return LLMResult(data=data, text=json.dumps(data), backend=self.name, model=self.model,
                         input_tokens=1000, output_tokens=100, seconds=0.01)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0] for t in texts]


def make_config(tmp: Path, **cut: Any) -> Config:
    data = {
        "llm": {
            "preset": "local",
            "fallback": "paid",
            "session_cap_usd": 1.0,
            "presets": {"local": {"default": "fake"}, "api": {"default": "paid"}},
            "backends": {"fake": {"type": "ollama", "model": "fake-1"}, "paid": {"type": "anthropic", "model": "paid-1"}},
            "pricing": {"paid-1": {"input": 10.0, "output": 50.0}},
            "est_output_tokens": {"underline": 100, "highlight": 100, "emphasis": 100, "tag": 50, "readback": 50},
        },
        "cut": {"density": "normal", "emphasis": True, "acronym_rule": True,
                "densities": {"normal": {"underline_pct": 35, "highlight_pct": 38, "emphasis_pct": 30}}, **cut},
        "readback": {"enabled": True, "min_score": 4, "max_retries": 2},
        "retrieval": {"enabled": False, "k": 2, "example_format": "markup", "max_prompt_tokens": 24000},
        "cite": {"default_format": "inline", "missing_marker": "[{field}?]", "extract_quals": True},
        "render": {"highlight_color": "yellow", "shrink_unread_pt": 8},
        "verify": {"allow_mid_sentence_end": True},
        "eval": {"runs_dir": "runs", "reports_dir": "reports", "holdout_path": "holdout.json"},
    }
    path = tmp / "config.toml"
    path.write_text("# test config\n")
    return Config(path=path, data=data)


def registry_with(cfg: Config, **backends: FakeBackend) -> Registry:
    reg = Registry(cfg)
    reg._built.update(backends)
    return reg
