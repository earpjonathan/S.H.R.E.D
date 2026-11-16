"""Argument context: a case/file .docx turned into a compact outline, plus the role the
new card must play ("internal link: legal certainty -> private biotech investment").
The outline goes to the locate, underline, highlight, and tag stages so the card fills a
gap in the chain instead of repeating an existing card."""
from __future__ import annotations

from pathlib import Path

from cardcutter.corpus.parse import parse_docx
from cardcutter.llm import prompts, schemas
from cardcutter.llm.cost import estimate_tokens
from cardcutter.llm.registry import Registry

LEVEL_NAMES = {1: "POCKET", 2: "HAT", 3: "BLOCK", 4: "card"}


def outline_text(path: Path, max_tokens: int = 1500) -> str:
    doc = parse_docx(path)
    lines = []
    for level, text in doc.outline:
        text = " ".join(text.split())
        if level == 4:
            lines.append(f"{'  ' * 3}- {text}")
        else:
            lines.append(f"{'  ' * (level - 1)}{LEVEL_NAMES[level]}: {text}")
    out = "\n".join(lines)
    while estimate_tokens(out) > max_tokens and len(lines) > 10:
        lines = lines[: int(len(lines) * 0.85)]  # keep the start of the case; note the cut
        out = "\n".join(lines) + "\n  … (outline truncated)"
    return out


def context_block(outline: str = "", role: str = "") -> str:
    parts = []
    if role:
        parts.append(f"This card's job in the case: {role}")
    if outline:
        parts.append("Existing case outline (don't repeat a card that's already there; fill the gap):\n" + outline)
    return "\n".join(parts)


def suggest_gaps(reg: Registry, outline: str) -> list[dict]:
    res = reg.call("tag", prompts.render("suggest", outline=outline), schemas.SUGGEST, "suggest")
    return res.data["gaps"]
