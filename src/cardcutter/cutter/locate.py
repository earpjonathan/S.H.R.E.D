"""Locate: pick the best run of paragraphs to cut the card from (top 3 candidates).

Short documents (under cut.max_window_words) are one candidate with no model call.
Long articles are first narrowed with embeddings (paragraphs most similar to the claim,
plus their neighbors) so the prompt fits the model's context window.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from cardcutter.config import Config
from cardcutter.cutter.pipeline import prompt_budget
from cardcutter.ingest.source import Document
from cardcutter.llm import prompts, schemas
from cardcutter.llm.cost import estimate_tokens
from cardcutter.llm.registry import Registry


@dataclass
class Candidate:
    start_para: int
    end_para: int
    char_start: int
    char_end: int
    words: int
    rationale: str


_SENT_END = re.compile(r"[.!?…][\"'”’)\]]*(?:\s?\[?\d{1,3}\]?)?$")   # a footnote number may follow: "stagnation.8"


def is_prose(p: str) -> bool:
    """A real sentence-bearing paragraph, not a heading, menu item, or citation-export list."""
    return len(p.split()) >= 8 and bool(_SENT_END.search(p.strip()))


def _trim_edges(doc: Document, a: int, b: int) -> tuple[int, int]:
    while a < b and not is_prose(doc.paragraphs[a]):
        a += 1
    while b > a and not is_prose(doc.paragraphs[b]):
        b -= 1
    return a, b


def _widen(doc: Document, a: int, b: int, used: set[int], *, min_words: int, max_words: int) -> tuple[int, int]:
    """Short windows get the following prose paragraphs (or, at the end, the preceding ones) until they
    reach min_words, without passing max_words or touching another candidate. A short subheading
    between two prose paragraphs is stepped over (it stays in the card, unread)."""
    words = lambda x, y: sum(len(doc.paragraphs[i].split()) for i in range(x, y + 1))  # noqa: E731
    n = len(doc.paragraphs)

    def step(edge: int, way: int) -> int | None:
        k = edge + way
        if not 0 <= k < n or k in used or _is_tail(doc, k):
            return None
        if is_prose(doc.paragraphs[k]):
            return k
        k2 = k + way
        if len(doc.paragraphs[k].split()) <= 12 and 0 <= k2 < n and k2 not in used \
                and is_prose(doc.paragraphs[k2]) and not _is_tail(doc, k2):
            return k2
        return None

    while words(a, b) < min_words:
        nb, na = step(b, 1), step(a, -1)
        if nb is not None and words(a, nb) <= max_words:
            b = nb
        elif na is not None and words(na, b) <= max_words:
            a = na
        else:
            break
    return a, b


_BIO_HEAD = re.compile(r"^\s*(?:about the authors?|about the writer|author bio(?:graphy)?|more from|related(?: articles| stories)?|"
                       r"read more|see also|share this|subscribe)\b", re.I)


_BOILER = re.compile(r"\b(?:the views (?:and opinions )?expressed|opinions expressed (?:here|in this)|click here|"
                     r"sign up for|send us yours|submit an op-?ed|all rights reserved|"
                     r"this (?:article|story|piece) (?:was |first )?(?:originally )?(?:published|appeared))\b", re.I)
_BIO_LEAD = re.compile(r"^\W*(?:(?:dr|prof)\.?\s+)?([A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*){0,4})\s+(?:is|was|holds|"
                       r"serves|served|has|recently|graduated|received|works|teaches|writes|covers|directs|leads)\b")


def _is_tail(doc: Document, i: int) -> bool:
    """The author box, a bio paragraph ("Jane Doe holds a degree in ..."), or site boilerplate
    ("The views expressed here ...", "Click here to submit an op-ed")."""
    p = doc.paragraphs[i]
    if _BIO_HEAD.match(p) or _BOILER.search(p):
        return True
    m = _BIO_LEAD.match(p)
    if not m or i < len(doc.paragraphs) // 2:
        return False
    names = {w.lower().strip(".") for a in (doc.meta.authors or []) for w in a.split() if len(w) > 2}
    return bool(names & {w.lower().strip(".") for w in m.group(1).split()})


def _before_bio(doc: Document, a: int, b: int) -> int:
    """A card never runs into the author box, a bio, or "related stories": end before them."""
    for i in range(a + 1, b + 1):
        if _is_tail(doc, i):
            return i - 1
    return b


def _candidate(doc: Document, a: int, b: int, why: str) -> Candidate:
    a, b = _trim_edges(doc, a, _before_bio(doc, a, b))
    spans = doc.para_spans()
    return Candidate(a, b, spans[a][0], spans[b][1], sum(len(doc.paragraphs[i].split()) for i in range(a, b + 1)), why)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def _overlap(claim: str, para: str) -> float:
    c, p = _words(claim), _words(para)
    return len(c & p) / (len(c) ** 0.5 * max(len(p), 1) ** 0.5) if c and p else 0.0


def _shortlist(reg: Registry, doc: Document, claim: str, budget_words: int) -> list[int]:
    """Paragraph indices to show the model: most claim-similar first, then neighbors, in order."""
    if reg.has_stage("embed"):
        emb = reg.for_stage("embed")
        vecs = np.array(emb.embed([claim] + doc.paragraphs), dtype=np.float32)
        vecs /= np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)
        sims = vecs[1:] @ vecs[0]
    else:  # no embedding model in this preset: plain word overlap with the claim
        sims = np.array([_overlap(claim, p) for p in doc.paragraphs], dtype=np.float32)
    chosen: set[int] = set()
    words = 0
    for i in np.argsort(-sims):
        for j in (int(i) - 1, int(i), int(i) + 1):
            if 0 <= j < len(doc.paragraphs) and j not in chosen:
                chosen.add(j)
                words += len(doc.paragraphs[j].split())
        if words >= budget_words:
            break
    return sorted(chosen)


def locate(reg: Registry, cfg: Config, doc: Document, claim: str, *, context: str = "", k: int = 3,
           progress=None) -> list[Candidate]:
    max_words = int(cfg.get("cut.max_window_words", 650))
    min_words = int(cfg.get("cut.min_window_words", 150))
    if not doc.paragraphs:
        raise ValueError("the source has no text")
    if doc.words <= min(min_words, max_words):  # short enough to be one card as it is
        return [_candidate(doc, 0, len(doc.paragraphs) - 1, "whole text")]
    if progress:
        progress("locate")
    budget_tokens = prompt_budget(reg, cfg, "locate")
    show = list(range(len(doc.paragraphs)))
    if estimate_tokens(doc.text) > budget_tokens * 0.8:
        show = _shortlist(reg, doc, claim, budget_words=int(budget_tokens * 0.8 * 3.6 / 6.5))
    lines = []
    prev = None
    for i in show:
        if prev is not None and i != prev + 1:
            lines.append("…")
        lines.append(f"[{i}] ({len(doc.paragraphs[i].split())} words) {doc.paragraphs[i]}")
        prev = i
    ctx = f"\nArgument context:\n{context}\n" if context else ""
    p = prompts.render("locate", claim=claim, context=ctx, paragraphs="\n\n".join(lines), max_words=max_words)
    res = reg.call("locate", p, schemas.LOCATE, "locate")
    allowed = set(show)
    out: list[Candidate] = []
    used: set[int] = set()
    for c in res.data["candidates"]:
        a, b = sorted((int(c["start"]), int(c["end"])))
        a, b = max(a, 0), min(b, len(doc.paragraphs) - 1)
        if a > b or a not in allowed:
            continue
        b = _before_bio(doc, a, b)
        # trim from the end until the window fits
        while b > a and sum(len(doc.paragraphs[i].split()) for i in range(a, b + 1)) > max_words:
            b -= 1
        if used & set(range(a, b + 1)):
            continue
        a, b = _widen(doc, a, b, used, min_words=min(min_words, max_words), max_words=max_words)
        used |= set(range(a, b + 1))
        out.append(_candidate(doc, a, b, str(c.get("rationale", ""))[:300]))
        if len(out) == k:
            break
    if not out:
        raise ValueError("locate found no usable window; try a more specific claim")
    return out
