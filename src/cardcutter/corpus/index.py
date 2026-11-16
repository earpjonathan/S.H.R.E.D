"""Embedding index of the human-cut corpus, used to pick few-shot examples.

Each card is embedded as its tag plus a short summary (the highlighted read-aloud text,
or the underlined text when nothing is highlighted). Vectors live in a plain .npz file
with the embedding model's name; the index is rebuilt automatically when the model or
the corpus changes. Holdout cards are left out so the eval never sees its own answers.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from cardcutter.card import Card

SUMMARY_WORDS = 80


def card_summary(c: Card) -> str:
    text = c.body.read_aloud()
    if not text:
        text = "".join(ch for ch, u in zip(c.body.text, c.body.marks["underline"]) if u)
    words = " ".join(text.split()).split(" ")
    return f"{c.tag.text.strip()}\n{' '.join(words[:SUMMARY_WORDS])}"


def query_text(claim: str, source: str, words: int = 150) -> str:
    return f"{claim.strip()}\n{' '.join(source.split()[:words])}"


def _corpus_hash(ids: list[str]) -> str:
    return hashlib.sha1("\n".join(ids).encode()).hexdigest()[:12]


@dataclass
class CardIndex:
    model: str
    ids: list[str]
    vecs: np.ndarray  # (n, d), L2-normalized float32

    @staticmethod
    def _norm(v: np.ndarray) -> np.ndarray:
        v = v.astype(np.float32)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)

    @classmethod
    def build(cls, model: str, cards: list[Card], embed: Callable[[list[str]], list[list[float]]],
              batch: int = 64, progress: Callable[[str], None] | None = None) -> "CardIndex":
        seen: set[str] = set()
        keep: list[Card] = []
        for c in cards:  # one entry per distinct body (the 1AC repeats Aff cards)
            key = body_key(c)
            if key not in seen:
                seen.add(key)
                keep.append(c)
        vecs: list[list[float]] = []
        for i in range(0, len(keep), batch):
            vecs.extend(embed([card_summary(c) for c in keep[i:i + batch]]))
            if progress:
                progress(f"embedded {min(i + batch, len(keep))}/{len(keep)}")
        return cls(model=model, ids=[c.meta["id"] for c in keep], vecs=cls._norm(np.array(vecs)))

    def save(self, path: Path, corpus_ids: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {"model": self.model, "corpus": _corpus_hash(corpus_ids)}
        np.savez_compressed(path, vecs=self.vecs, ids=np.array(self.ids), meta=np.array(json.dumps(meta)))

    @classmethod
    def load(cls, path: Path) -> tuple["CardIndex", dict]:
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        return cls(model=meta["model"], ids=[str(x) for x in z["ids"]], vecs=z["vecs"]), meta

    def search(self, qvec: list[float], k: int, exclude: set[str] = frozenset()) -> list[tuple[str, float]]:
        q = self._norm(np.array([qvec]))[0]
        scores = self.vecs @ q
        out = []
        for i in np.argsort(-scores):
            cid = self.ids[int(i)]
            if cid in exclude:
                continue
            out.append((cid, float(scores[int(i)])))
            if len(out) == k:
                break
        return out


def body_key(c: Card) -> str:
    return " ".join(c.body.text.split())


def excluded_ids(cards: list[Card], exclude: set[str]) -> set[str]:
    """`exclude` plus every card whose body matches an excluded card's body (the 1AC repeats
    Aff cards under other ids), so a holdout card can't come back as its own example."""
    banned = {body_key(c) for c in cards if c.meta["id"] in exclude}
    return set(exclude) | {c.meta["id"] for c in cards if body_key(c) in banned}


def load_or_build(cfg, embed_backend, cards: list[Card],
                  progress: Callable[[str], None] | None = None) -> CardIndex:
    """Load data/index.npz if it was built with the same embedding model and corpus; else rebuild.
    The index covers the whole corpus; holdout cards are filtered out at search time."""
    path = cfg.path_of("retrieval.index_path")
    ids = [c.meta["id"] for c in cards]
    if path.exists():
        idx, meta = CardIndex.load(path)
        if meta.get("model") == embed_backend.model and meta.get("corpus") == _corpus_hash(ids):
            return idx
        if progress:
            progress(f"index was built with {meta.get('model')} / another corpus; rebuilding")
    elif progress:
        progress(f"building the example index with {embed_backend.model} (one time)")
    idx = CardIndex.build(embed_backend.model, cards, embed_backend.embed, progress=progress)
    idx.save(path, ids)
    return idx
