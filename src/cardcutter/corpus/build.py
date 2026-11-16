"""`cardcutter corpus build`: parse every example .docx into cards.jsonl plus stats."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cardcutter.card import Card
from cardcutter.config import Config
from cardcutter.corpus.parse import parse_docx
from cardcutter.corpus.stats import compute_stats, stats_markdown


def example_files(cfg: Config) -> list[Path]:
    d = cfg.path_of("corpus.examples_dir")
    files = sorted(p for p in d.glob("*.docx") if not p.name.startswith("~$"))
    if not files:
        raise FileNotFoundError(f"No .docx files in {d}")
    return files


def build_corpus(cfg: Config) -> tuple[list[Card], dict[str, Any]]:
    cards: list[Card] = []
    parse_info = {"files": 0, "analytics": 0, "tag_only": 0, "no_cite13": 0, "tables_skipped": 0}
    for f in example_files(cfg):
        doc = parse_docx(f)
        cards.extend(doc.cards)
        parse_info["files"] += 1
        parse_info["analytics"] += len(doc.analytics)
        parse_info["tag_only"] += doc.tag_only
        parse_info["no_cite13"] += doc.no_cite13
        parse_info["tables_skipped"] += doc.tables_skipped

    out = cfg.path_of("corpus.cards_path")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for c in cards:
            fh.write(json.dumps(c.to_json(), ensure_ascii=False) + "\n")

    stats = compute_stats(cards, extra=parse_info)
    cfg.path_of("corpus.stats_json").write_text(json.dumps(stats, indent=1, ensure_ascii=False), encoding="utf-8")
    cfg.path_of("corpus.stats_md").write_text(stats_markdown(stats), encoding="utf-8")
    return cards, stats


def load_cards(cfg: Config) -> list[Card]:
    path = cfg.path_of("corpus.cards_path")
    with open(path, encoding="utf-8") as fh:
        return [Card.from_json(json.loads(line)) for line in fh if line.strip()]
