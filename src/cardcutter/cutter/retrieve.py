"""Few-shot example retrieval shared by `cut`, the eval, and the web UI."""
from __future__ import annotations

from typing import Callable

from cardcutter.card import Card
from cardcutter.config import Config
from cardcutter.corpus.index import CardIndex, body_key, excluded_ids, load_or_build, query_text
from cardcutter.llm.registry import Registry


class Retriever:
    def __init__(self, cfg: Config, reg: Registry, cards: list[Card], exclude: set[str] = frozenset(),
                 progress: Callable[[str], None] | None = None):
        self.cfg, self.reg = cfg, reg
        self.by_id = {c.meta["id"]: c for c in cards}
        self.exclude = excluded_ids(cards, set(exclude))
        self.k = int(cfg.get("retrieval.k", 4))
        self.embedder = reg.for_stage("embed")
        self.index: CardIndex = load_or_build(cfg, self.embedder, cards, progress=progress)

    def examples(self, claim: str, source: str, k: int | None = None) -> list[Card]:
        qvec = self.embedder.embed([query_text(claim, source)])[0]
        norm_source = " ".join(source.split())
        out: list[Card] = []
        for cid, _score in self.index.search(qvec, (k or self.k) + 3, exclude=self.exclude):
            c = self.by_id.get(cid)
            if c is None or body_key(c) == norm_source:
                continue
            out.append(c)
            if len(out) == (k or self.k):
                break
        return out
