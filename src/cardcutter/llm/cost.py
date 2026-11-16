"""Token and cost accounting, estimates before a run, and an optional session cap."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SpendingCapExceeded(Exception):
    pass


@dataclass(frozen=True)
class Price:
    input: float   # USD per 1M input tokens
    output: float  # USD per 1M output tokens (thinking tokens bill as output)


def estimate_tokens(text: str) -> int:
    """Rough token count for estimates only (about 3.6 chars per token for English)."""
    return max(1, int(len(text) / 3.6))


@dataclass
class CostTracker:
    prices: dict[str, Price] = field(default_factory=dict)
    session_cap_usd: float | None = None
    spent_usd: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def from_config(cls, cfg) -> "CostTracker":
        table: dict[str, Any] = cfg.get("llm.pricing", {}) or {}
        prices = {model: Price(float(p.get("input", 0)), float(p.get("output", 0))) for model, p in table.items()}
        cap = cfg.get("llm.session_cap_usd")
        return cls(prices=prices, session_cap_usd=float(cap) if cap else None)

    def price_of(self, model: str) -> Price | None:
        return self.prices.get(model)

    def cost(self, model: str, input_tokens: int, output_tokens: int, free: bool = False) -> float:
        if free:
            return 0.0
        p = self.price_of(model)
        if p is None:
            return 0.0
        return (input_tokens * p.input + output_tokens * p.output) / 1_000_000

    def would_exceed(self, extra_usd: float) -> bool:
        return self.session_cap_usd is not None and self.spent_usd + extra_usd > self.session_cap_usd

    def guard(self, estimate_usd: float) -> None:
        if self.would_exceed(estimate_usd):
            raise SpendingCapExceeded(
                f"next call (~${estimate_usd:.4f}) would pass the session cap "
                f"${self.session_cap_usd:.2f} (spent ${self.spent_usd:.4f})"
            )

    def record(self, cost_usd: float, input_tokens: int, output_tokens: int) -> None:
        self.spent_usd += cost_usd
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
