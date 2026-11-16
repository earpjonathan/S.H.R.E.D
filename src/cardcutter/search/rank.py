"""Rank fetched articles by how well their best passage matches the claim.

Each article is split into ~160-word chunks; the claim and the chunks are embedded with the
local embedding model (word overlap if the preset has none). An article's relevance is
driven by its best chunk, with small bonuses for things a debater wants in a cite: a named
author, a date, recency, and scholarly provenance.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import numpy as np

from cardcutter.cite.meta import parse_date
from cardcutter.ingest.source import Document
from cardcutter.llm.registry import Registry
from cardcutter.search.fetch import Fetched
from cardcutter.search.providers import SearchHit, domain

_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class Source:
    id: str
    doc: Document
    hit: SearchHit
    relevance: float
    score: float
    best_paras: tuple[int, int]
    reasons: list[str] = field(default_factory=list)
    stance: int | None = None        # 1-5 from the stance check (0 = argues against / off topic)
    credible: int | None = None      # 1-5 from the stance check: how far a judge would trust the source

    def summary(self) -> dict:
        m = self.doc.meta
        return {"id": self.id, "title": m.title or self.hit.title, "url": self.doc.origin, "domain": domain(self.doc.origin),
                "publication": m.publication, "date": m.date, "authors": m.authors[:3], "words": self.doc.words,
                "provider": self.hit.provider, "relevance": round(self.relevance, 3), "score": round(self.score, 3),
                "best_paras": list(self.best_paras), "reasons": self.reasons, "stance": self.stance, "credible": self.credible,
                "snippet": " ".join(" ".join(self.doc.paragraphs[self.best_paras[0]:self.best_paras[1] + 1]).split()[:60])}


def chunks(doc: Document, target_words: int = 160) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    start, words, buf = 0, 0, []
    for i, p in enumerate(doc.paragraphs):
        buf.append(p)
        words += len(p.split())
        if words >= target_words:
            out.append((start, i, " ".join(buf)))
            start, words, buf = i + 1, 0, []
    if buf:
        out.append((start, len(doc.paragraphs) - 1, " ".join(buf)))
    return out


def _overlap(a: str, b: str) -> float:
    x = {w for w in _WORD.findall(a.lower()) if len(w) > 3}
    y = {w for w in _WORD.findall(b.lower()) if len(w) > 3}
    return len(x & y) / (len(x) ** 0.5 * len(y) ** 0.5) if x and y else 0.0


def quality_bonus(doc: Document, hit: SearchHit, today: date | None = None) -> tuple[float, list[str]]:
    today = today or date.today()
    bonus, why = 0.0, []
    if doc.meta.authors:
        bonus += 0.03
        why.append("named author")
    d = parse_date(doc.meta.date)
    if d.year:
        bonus += 0.01
        age = today.year - d.year
        if age <= 3:
            bonus += 0.02
            why.append(f"recent ({d.year})")
        else:
            why.append(str(d.year))
    if hit.provider == "openalex" or doc.meta.doi:
        bonus += 0.02
        why.append("scholarly")
    if "sponsored" in doc.flags:
        bonus -= 0.10
        why.append("sponsored content")
    return bonus, why


def rank_sources(reg: Registry, claim: str, fetched: list[Fetched], *, max_chunks: int = 30,
                 say: Callable[[str], None] = lambda _m: None) -> list[Source]:
    docs = [(f.hit, f.doc) for f in fetched if f.doc is not None]
    if not docs:
        return []
    per_doc: list[list[tuple[int, int, str]]] = []
    for _, doc in docs:
        cs = chunks(doc)
        if len(cs) > max_chunks:  # long PDFs: pre-filter by word overlap, embed only the likeliest chunks
            cs = sorted(cs, key=lambda c: -_overlap(claim, c[2]))[:max_chunks]
        per_doc.append(cs)
    texts = [c[2][:1500] for cs in per_doc for c in cs]
    if reg.has_stage("embed"):
        say(f"ranking {len(docs)} articles ({len(texts)} passages)")
        emb = reg.for_stage("embed")
        vecs: list[list[float]] = []
        for i in range(0, len(texts), 128):
            vecs.extend(emb.embed(texts[i:i + 128]))
        q = np.array(emb.embed([claim])[0], dtype=np.float32)
        m = np.array(vecs, dtype=np.float32)
        m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
        sims = m @ (q / max(float(np.linalg.norm(q)), 1e-9))
    else:
        sims = np.array([_overlap(claim, t) for t in texts], dtype=np.float32)
    out: list[Source] = []
    k = 0
    for (hit, doc), cs in zip(docs, per_doc):
        s = sims[k:k + len(cs)]
        k += len(cs)
        order = np.argsort(-s)
        top1 = float(s[order[0]])
        top2 = float(s[order[1]]) if len(order) > 1 else top1
        relevance = 0.75 * top1 + 0.25 * top2
        bonus, why = quality_bonus(doc, hit)
        best = cs[int(order[0])]
        sid = hashlib.sha1(doc.origin.encode()).hexdigest()[:12]
        out.append(Source(sid, doc, hit, relevance, relevance + bonus, (best[0], best[1]), why))
    out.sort(key=lambda x: -x.score)
    return out
