"""HTML preview that mimics Verbatim's look (used by the web UI and for quick checks)."""
from __future__ import annotations

import html
from typing import Any, Iterable

from cardcutter.card import LINE_BREAK, Card, FText

HIGHLIGHT_CSS = {
    "yellow": "#ffff00", "cyan": "#00ffff", "green": "#00ff00", "lightGray": "#d3d3d3",
    "magenta": "#ff00ff", "red": "#ff0000", "blue": "#0000ff", "darkYellow": "#808000",
    "darkCyan": "#008080", "darkGray": "#a9a9a9", "black": "#000000", "white": "#ffffff",
}

CSS = """
.cc-doc { font-family: Calibri, Carlito, "Segoe UI", Arial, sans-serif; font-size: 11pt; color: #000;
          background: #fff; padding: 24px 32px; line-height: 1.3; max-width: 8.5in; }
.cc-tag { font-size: 13pt; font-weight: bold; margin: 18px 0 6px; }
.cc-undertag { font-size: 12pt; font-style: italic; color: #385623; margin: 0 0 4px; }
.cc-cite { margin: 0 0 6px; }
.cc-body p { margin: 0 0 8px; }
.cc-u { text-decoration: underline; }
.cc-e { font-weight: bold; text-decoration: underline; outline: 1px solid #000; outline-offset: -1px; }
.cc-c13 { font-weight: bold; font-size: 13pt; }
"""


def _span(text: str, fmt: dict[str, Any], base_pt: float) -> str:
    classes = []
    style = []
    if fmt.get("cite13"):
        classes.append("cc-c13")
    if fmt.get("emphasis"):
        classes.append("cc-e")
    elif fmt.get("underline"):
        classes.append("cc-u")
    hl = fmt.get("highlight")
    if hl:
        color = "#" + hl[4:] if hl.startswith("shd:") else HIGHLIGHT_CSS.get(hl, "#ffff00")
        style.append(f"background:{color}")
    size = fmt.get("size")
    if size and not fmt.get("cite13") and abs(size / 2 - base_pt) > 0.01:
        style.append(f"font-size:{size / 2:g}pt")
    if fmt.get("bold") == "on":
        style.append("font-weight:bold")
    elif fmt.get("bold") == "off" and not fmt.get("emphasis"):
        style.append("font-weight:normal")
    if fmt.get("italic") == "on":
        style.append("font-style:italic")
    if fmt.get("strike"):
        style.append("text-decoration:line-through")
    body = html.escape(text).replace(LINE_BREAK, "<br>")
    if fmt.get("vert") == "superscript":
        body = f"<sup>{body}</sup>"
    elif fmt.get("vert") == "subscript":
        body = f"<sub>{body}</sub>"
    if not classes and not style:
        return body
    attrs = (f' class="{" ".join(classes)}"' if classes else "") + (f' style="{";".join(style)}"' if style else "")
    return f"<span{attrs}>{body}</span>"


def ftext_html(ft: FText, base_pt: float = 11) -> str:
    return "".join(_span(ft.text[s:e], fmt, base_pt) for s, e, fmt in ft.runs())


def card_html(card: Card) -> str:
    parts = [f'<div class="cc-tag">{ftext_html(card.tag, 13)}</div>']
    if card.meta.get("undertag"):
        parts.append(f'<div class="cc-undertag">{ftext_html(FText.from_json(card.meta["undertag"]), 12)}</div>')
    if card.cite.text:
        parts.append(f'<div class="cc-cite">{ftext_html(card.cite)}</div>')
    parts.append('<div class="cc-body">' + "".join(f"<p>{ftext_html(p)}</p>" for p in card.body.paragraphs()) + "</div>")
    return "\n".join(parts)


def page_html(cards: Iterable[Card], title: str = "CardCutter preview") -> str:
    inner = "\n".join(card_html(c) for c in cards)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
        f"<style>body{{background:#fff;margin:0}}{CSS}</style></head>"
        f"<body><div class='cc-doc'>{inner}</div></body></html>"
    )
