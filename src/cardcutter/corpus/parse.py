"""Parse Verbatim .docx files into cards with per-character formatting masks.

Structure (Verbatim template):
- Heading 1 = pocket, Heading 2 = hat, Heading 3 = block, Heading 4 = tag.
- A card is a Heading 4 tag, then a cite paragraph, then body paragraphs up to the
  next heading. `Analytic` paragraphs (based on Heading 4) are analytics, not cards.
- An optional `Undertag` paragraph between tag and cite is kept in meta.

Formatting is resolved the way Word does it (docDefaults <- paragraph style <-
character style <- direct formatting), because teams underline both with the
`StyleUnderline` style and with direct `w:u`.
"""
from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from cardcutter.card import Card, FText
from cardcutter.ooxml import (
    OFF_VALUES,
    W,
    RunProps,
    StyleSheet,
    iter_runs,
    p_style,
    r_style,
    run_text,
    val,
)

HEADING_LEVELS = {"heading 1": 1, "heading 2": 2, "heading 3": 3, "heading 4": 4, "heading 5": 5, "heading 6": 6}
ANALYTIC_NAMES = {"analytic", "analytics"}
NO_HIGHLIGHT_FILLS = {"auto", "ffffff", "none", ""}


def run_format(sheet: StyleSheet, pstyle: str | None, r: etree._Element) -> dict[str, Any]:
    direct = RunProps.from_rpr(r.find(f"{W}rPr"))
    rs = r_style(r)
    eff = sheet.effective(pstyle, rs, direct)
    rs_name = sheet.name(rs)

    highlight: str | None = None
    if eff.highlight and eff.highlight.lower() != "none":
        highlight = eff.highlight
    elif eff.shd_fill and eff.shd_fill.lower() not in NO_HIGHLIGHT_FILLS:
        highlight = "shd:" + eff.shd_fill.upper()

    return {
        "underline": bool(eff.u) and eff.u.lower() not in OFF_VALUES,
        "emphasis": rs_name == "emphasis" or bool(direct.bdr),
        "cite13": rs_name == "style 13 pt bold",
        "strike": bool(direct.strike),
        "highlight": highlight,
        "size": eff.sz,
        "vert": direct.vert if direct.vert and direct.vert != "baseline" else None,
        "bold": None if direct.b is None else ("on" if direct.b else "off"),
        "italic": None if direct.i is None else ("on" if direct.i else "off"),
    }


def paragraph_ftext(sheet: StyleSheet, p: etree._Element) -> FText:
    ps = p_style(p)
    ft = FText("")
    for r in iter_runs(p):
        t = run_text(r)
        if t:
            ft.append(t, run_format(sheet, ps, r))
    return ft


@dataclass
class Para:
    kind: str  # "heading" | "analytic" | "undertag" | "text" | "table"
    level: int
    ftext: FText
    style: str


@dataclass
class ParsedDoc:
    path: Path
    cards: list[Card] = field(default_factory=list)
    analytics: list[str] = field(default_factory=list)
    outline: list[tuple[int, str]] = field(default_factory=list)
    tag_only: int = 0          # Heading 4 with no card under it
    no_cite13: int = 0         # cards whose cite has no Style13ptBold run
    tables_skipped: int = 0


def read_docx(path: Path) -> tuple[StyleSheet, etree._Element]:
    with zipfile.ZipFile(path) as z:
        sheet = StyleSheet.from_xml(z.read("word/styles.xml"))
        doc = etree.fromstring(z.read("word/document.xml"))
    return sheet, doc


def classify(sheet: StyleSheet, p: etree._Element) -> Para:
    ps = p_style(p)
    name = sheet.name(ps)
    ft = paragraph_ftext(sheet, p)
    if name in ANALYTIC_NAMES:
        return Para("analytic", 4, ft, name)
    if name in HEADING_LEVELS:
        return Para("heading", HEADING_LEVELS[name], ft, name)
    if name == "undertag":
        return Para("undertag", 0, ft, name)
    return Para("text", 0, ft, name)


def body_paragraphs(sheet: StyleSheet, doc: etree._Element) -> list[Para]:
    body = doc.find(f"{W}body")
    out: list[Para] = []
    for el in body:
        if el.tag == f"{W}p":
            out.append(classify(sheet, el))
        elif el.tag == f"{W}tbl":
            out.append(Para("table", 0, FText(""), "table"))
        elif el.tag == f"{W}sdt":
            content = el.find(f"{W}sdtContent")
            if content is not None:
                for p in content.iter(f"{W}p"):
                    out.append(classify(sheet, p))
    return out


def _card_id(file: str, n: int, tag: str, body: str) -> str:
    h = hashlib.sha1(f"{tag}\x00{body}".encode()).hexdigest()[:8]
    return f"{Path(file).stem}#{n}-{h}"


def parse_docx(path: Path) -> ParsedDoc:
    path = Path(path)
    sheet, doc = read_docx(path)
    paras = body_paragraphs(sheet, doc)
    out = ParsedDoc(path=path)
    heads: dict[int, str] = {}

    i = 0
    n = len(paras)
    while i < n:
        p = paras[i]
        if p.kind == "table":
            out.tables_skipped += 1
            i += 1
            continue
        if p.kind == "analytic":
            if p.ftext.text.strip():
                out.analytics.append(p.ftext.text.strip())
            i += 1
            continue
        if p.kind != "heading":
            i += 1
            continue
        text = p.ftext.text.strip()
        if p.level < 4:
            if text:
                heads[p.level] = text
                for deeper in [k for k in heads if k > p.level]:
                    del heads[deeper]
                out.outline.append((p.level, text))
            i += 1
            continue
        if p.level > 4 or not text:
            i += 1
            continue

        # Heading 4: a tag. Collect undertag, cite, body until the next heading/analytic.
        tag = p.ftext
        out.outline.append((4, text))
        j = i + 1
        undertag: FText | None = None
        under: list[FText] = []
        while j < n and paras[j].kind not in ("heading", "analytic"):
            q = paras[j]
            if q.kind == "table":
                out.tables_skipped += 1
            elif q.ftext.text.strip():
                if q.kind == "undertag" and not under and undertag is None:
                    undertag = q.ftext
                else:
                    under.append(q.ftext)
            j += 1

        # Which paragraph is the cite? Normally the first one, marked by Style13ptBold.
        # If the first paragraph is already cut (underlined/highlighted), the card has
        # no cite; if it is plain text, treat it as an unstyled cite.
        flags: list[str] = []
        cite: FText | None = None
        body: list[FText] = []
        if under:
            first = under[0]
            if any(first.marks["cite13"]):
                cite, body = first, under[1:]
            elif any(first.marks["underline"]) or any(h is not None for h in first.marks["highlight"]):
                cite, body = FText(""), under
                flags.append("no_cite")
            else:
                cite, body = first, under[1:]
                flags.append("cite_unstyled")

        if cite is None or not body:
            out.tag_only += 1
        else:
            if "cite_unstyled" in flags or "no_cite" in flags:
                out.no_cite13 += 1
            b = FText.concat(body)
            meta: dict[str, Any] = {
                "id": _card_id(path.name, len(out.cards), tag.text, b.text),
                "file": path.name,
                "pocket": heads.get(1, ""),
                "hat": heads.get(2, ""),
                "block": heads.get(3, ""),
                "n_paragraphs": len(body),
                "flags": flags,
            }
            if undertag is not None:
                meta["undertag"] = undertag.to_json()
            out.cards.append(Card(tag=tag, cite=cite, body=b, meta=meta))
        i = j
    return out


def parse_many(paths: list[Path]) -> list[ParsedDoc]:
    return [parse_docx(p) for p in paths]


__all__ = ["parse_docx", "parse_many", "ParsedDoc", "run_format", "paragraph_ftext", "val"]
