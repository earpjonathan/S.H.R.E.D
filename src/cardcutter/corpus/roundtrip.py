"""Prove the parser and renderer are lossless: parse -> render -> parse must match."""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from cardcutter.card import ATTRS, Card
from cardcutter.corpus.parse import parse_docx
from cardcutter.render.docx import write_new
from cardcutter.render.template import build_template


@dataclass
class RoundTripResult:
    checked: int = 0
    mismatches: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches and self.checked > 0


def _comparable(c: Card) -> dict:
    return {
        "tag": c.tag.to_json(),
        "cite": c.cite.to_json(),
        "body": c.body.to_json(),
        "undertag": c.meta.get("undertag"),
    }


def first_difference(a: Card, b: Card) -> str:
    for part in ("tag", "cite", "body"):
        fa, fb = getattr(a, part), getattr(b, part)
        if fa.text != fb.text:
            i = next((k for k, (x, y) in enumerate(zip(fa.text, fb.text)) if x != y), min(len(fa.text), len(fb.text)))
            return f"{part} text differs at {i}: {fa.text[i:i+30]!r} vs {fb.text[i:i+30]!r}"
        for attr in ATTRS:
            if fa.marks[attr] != fb.marks[attr]:
                i = next(k for k, (x, y) in enumerate(zip(fa.marks[attr], fb.marks[attr])) if x != y)
                return f"{part}.{attr} differs at {i} ({fa.text[max(0,i-10):i+10]!r}): {fa.marks[attr][i]!r} vs {fb.marks[attr][i]!r}"
    return "undertag differs"


def roundtrip(cards: list[Card], template: Path, out_path: Path) -> RoundTripResult:
    write_new(cards, template, out_path)
    back = parse_docx(out_path).cards
    res = RoundTripResult(checked=len(cards))
    if len(back) != len(cards):
        res.mismatches.append(f"wrote {len(cards)} cards, read back {len(back)}")
        return res
    for a, b in zip(cards, back):
        if json.dumps(_comparable(a), sort_keys=True) != json.dumps(_comparable(b), sort_keys=True):
            res.mismatches.append(f"{a.meta.get('id')}: {first_difference(a, b)}")
    return res


def roundtrip_corpus(example_files: list[Path], cards: list[Card], workdir: Path | None = None) -> dict[str, RoundTripResult]:
    """Round-trip every card through a template built from its own source file."""
    results: dict[str, RoundTripResult] = {}
    with tempfile.TemporaryDirectory() as tmp:
        work = workdir or Path(tmp)
        for f in example_files:
            mine = [c for c in cards if c.meta.get("file") == f.name]
            tpl = build_template(f, work / f"{f.stem}.template.docx")
            results[f.name] = roundtrip(mine, tpl, work / f"{f.stem}.roundtrip.docx")
    return results
