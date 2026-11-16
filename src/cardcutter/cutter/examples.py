"""Turn retrieved human-cut cards into compact few-shot examples.

Two formats (config `retrieval.example_format`), compared in the eval:
- "markup":  _underlined_  [[highlighted]]  <E>emphasized</E>, long unformatted stretches
             shortened with "…". Compact; shows the cut the way a reader sees it.
- "indexed": the same index:word view the model answers in, followed by the ranges a
             human chose. Longer, but in exactly the model's answer format.
"""
from __future__ import annotations

import json

from cardcutter.card import Card
from cardcutter.cutter.rules import clean_ranges  # noqa: F401  (kept for symmetry with pipeline)
from cardcutter.tokenize import tokenize, word_ranges_from_mask

LEGEND = "_x_ = underlined, [[x]] = underlined and highlighted (read aloud), <E>x</E> = emphasized (boxed)"


def _shorten_plain(text: str, keep: int = 10, limit: int = 30) -> str:
    words = text.split(" ")
    if len(words) <= limit:
        return text
    return " ".join(words[:keep]) + " … " + " ".join(words[-keep:])


def markup(card: Card, max_words: int = 260) -> str:
    b = card.body
    ul, hl, em = b.marks["underline"], b.marks["highlight"], b.marks["emphasis"]
    out: list[str] = []
    i, n = 0, len(b.text)
    while i < n:
        state = (bool(ul[i]), hl[i] is not None, bool(em[i]))
        j = i + 1
        while j < n and (bool(ul[j]), hl[j] is not None, bool(em[j])) == state:
            j += 1
        seg = b.text[i:j].replace("\n", " ¶ ")
        u, h, e = state
        if not (u or h or e):
            seg = _shorten_plain(seg)
        else:
            lead = seg[: len(seg) - len(seg.lstrip())]
            trail = seg[len(seg.rstrip()):]
            core = seg.strip()
            if core:
                if h:
                    core = f"[[{core}]]"
                elif u:
                    core = f"_{core}_"
                if e:
                    core = f"<E>{core}</E>"
            seg = lead + core + trail
        out.append(seg)
        i = j
    text = "".join(out)
    words = text.split(" ")
    if len(words) > max_words:
        text = " ".join(words[:max_words]) + " …"
    return text


def indexed(card: Card, max_words: int = 220) -> str:
    b = card.body
    toks = tokenize(b.text)[:max_words]
    view = " ".join(f"{t.index}:{t.text}" for t in toks)
    ul = word_ranges_from_mask(b.marks["underline"], toks)
    hl = word_ranges_from_mask([h is not None for h in b.marks["highlight"]], toks)
    em = word_ranges_from_mask(b.marks["emphasis"], toks)
    rng = lambda rs: [{"start": a, "end": z} for a, z in rs]  # noqa: E731
    return f"{view}\nAnswer: " + json.dumps({"underline": rng(ul), "highlight": rng(hl), "emphasis": rng(em)})


def render_examples(cards: list[Card], fmt: str = "markup") -> str:
    if not cards:
        return ""
    parts = [f"Examples of how this team cuts similar cards ({LEGEND if fmt == 'markup' else 'word indices'}):"]
    for k, c in enumerate(cards, 1):
        body = markup(c) if fmt == "markup" else indexed(c)
        parts.append(f"\nExample {k}\nTag: {c.tag.text.strip()}\n{body}")
    return "\n".join(parts) + "\n"


def render_tag_examples(cards: list[Card]) -> str:
    if not cards:
        return ""
    lines = ["Tags this team wrote for similar cards (underlined words in _x_):"]
    for c in cards:
        t = c.tag
        s, prev = [], False
        for ch, u in zip(t.text, t.marks["underline"]):
            if u and not prev:
                s.append("_")
            if prev and not u:
                s.append("_")
            s.append(ch)
            prev = bool(u)
        if prev:
            s.append("_")
        lines.append(f"- {''.join(s).strip()}")
    return "\n".join(lines) + "\n"


def render_readback_examples(cards: list[Card], n: int = 3) -> str:
    """Reference cards for the read-back judge: this team's tag and read-aloud text."""
    picked = [c for c in cards if c.body.read_aloud()][:n]
    if not picked:
        return ""
    lines = ["Cards this team cut, read aloud. Treat these as good cards (4 to 5 on every score):"]
    for c in picked:
        words = c.body.read_aloud().split()
        lines.append(f"Tag: {' '.join(c.tag.text.split())}\nRead aloud: {' '.join(words[:90])}{' …' if len(words) > 90 else ''}")
    return "\n\n".join(lines) + "\n"


def render_trim_examples(cards: list[Card], n: int = 3) -> str:
    """How this team's similar cards read aloud, as a length and style reference for trimming."""
    picked = [c for c in cards if c.body.read_aloud()][:n]
    if not picked:
        return ""
    lines = ["How this team's cards on similar topics read aloud (match this compression):"]
    for c in picked:
        words = c.body.read_aloud().split()
        under = len(c.body.text.split())
        lines.append(f"- {len(words)} words read from a {under}-word card: {' '.join(words[:70])}{' …' if len(words) > 70 else ''}")
    return "\n".join(lines) + "\n"
