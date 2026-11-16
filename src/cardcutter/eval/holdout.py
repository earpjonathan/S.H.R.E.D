"""Choose the held-out eval cards.

Spec: about 100 corpus cards with the most unformatted text around the underlining,
so the cutter has real choices to make. We also require a clean card: a styled cite,
highlighting that sits inside underlining, and a body of 120-600 words. Duplicate
bodies (the 1AC repeats cards from the Aff file) are counted once.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cardcutter.card import Card
from cardcutter.tokenize import tokenize


def card_profile(c: Card) -> dict[str, float]:
    b = c.body
    ul, hl = b.marks["underline"], b.marks["highlight"]
    idx = [i for i, ch in enumerate(b.text) if not ch.isspace()]
    n = len(idx) or 1
    u = sum(1 for i in idx if ul[i])
    h = sum(1 for i in idx if hl[i] is not None)
    h_in = sum(1 for i in idx if hl[i] is not None and ul[i])
    return {
        "words": len(tokenize(b.text)),
        "unformatted": 1 - u / n,
        "ul_density": u / n,
        "hl_chars": h,
        "hl_nested": h_in / h if h else 0.0,
    }


def select_holdout(cards: list[Card], n: int = 100) -> list[dict[str, Any]]:
    seen: set[str] = set()
    pool = []
    for c in cards:
        key = " ".join(c.body.text.split())
        if key in seen:
            continue
        seen.add(key)
        if c.meta.get("flags"):
            continue
        p = card_profile(c)
        if not (120 <= p["words"] <= 600) or p["hl_chars"] == 0 or p["hl_nested"] < 0.98 or p["ul_density"] < 0.08:
            continue
        pool.append((p["unformatted"], c.meta["id"], p))
    pool.sort(key=lambda x: (-x[0], x[1]))
    return [{"id": cid, **{k: round(v, 4) for k, v in p.items()}} for _, cid, p in pool[:n]]


def save_holdout(entries: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"criteria": __doc__.strip(), "cards": entries}, indent=1), encoding="utf-8")


def load_holdout(path: Path) -> list[str]:
    return [e["id"] for e in json.loads(path.read_text(encoding="utf-8"))["cards"]]
