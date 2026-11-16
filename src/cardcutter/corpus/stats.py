"""Corpus statistics: densities, partial-word highlights, cite formats, colors, nesting.

Densities count non-whitespace characters only, so they do not depend on whether a
team's highlighting happens to cover the spaces between words.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from typing import Any

from cardcutter.card import Card, FText
from cardcutter.tokenize import tokenize

WORD_CORE = re.compile(r"\w+(?:[’'\-­]\w+)*")


def _dist(xs: list[float]) -> dict[str, float]:
    if not xs:
        return {"n": 0}
    xs = sorted(xs)

    def q(p: float) -> float:
        return xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))]

    return {
        "n": len(xs),
        "mean": round(statistics.fmean(xs), 4),
        "p10": round(q(0.10), 4),
        "p25": round(q(0.25), 4),
        "median": round(q(0.50), 4),
        "p75": round(q(0.75), 4),
        "p90": round(q(0.90), 4),
    }


def _nonws(ft: FText, pred) -> int:
    return sum(1 for i, ch in enumerate(ft.text) if not ch.isspace() and pred(i))


def partial_words(ft: FText, attr: str) -> list[dict[str, Any]]:
    """Words where some, but not all, letters carry `attr` (e.g. **biod**iversity)."""
    marks = ft.marks[attr]
    on = (lambda v: v is not None) if attr == "highlight" else bool
    out = []
    for m in WORD_CORE.finditer(ft.text):
        idx = [i for i in range(m.start(), m.end()) if ft.text[i].isalnum()]
        k = sum(1 for i in idx if on(marks[i]))
        if 0 < k < len(idx):
            sel = [on(marks[i]) for i in range(m.start(), m.end())]
            word = m.group()
            shown = "".join(c.upper() if s else c for c, s in zip(word, sel))
            if sel[0] and sum(sel) == 1:
                kind = "initial"
            elif sel[0] and "".join("1" if s else "0" for s in sel).rstrip("0").count("0") == 0:
                kind = "prefix"
            else:
                kind = "other"
            out.append({"word": word, "shown": shown, "kind": kind})
    return out


BRACKET_RE = re.compile(r"^\s*[\[(]")


def cite_format(card: Card) -> str:
    flags = card.meta.get("flags", [])
    if "no_cite" in flags:
        return "none"
    if "cite_unstyled" in flags:
        return "unstyled"
    cm = card.cite.marks["cite13"]
    last = max((i for i, v in enumerate(cm) if v), default=-1)
    rest = card.cite.text[last + 1:]
    return "bracketed" if BRACKET_RE.match(rest) else "inline"


def bracket_cutter(card: Card) -> str | None:
    """The last field of a bracketed cite, which holds the cutter's name."""
    t = card.cite.text.rstrip()
    if not t.endswith("]"):
        return None
    inner = t[: -1]
    field = re.split(r"[;,]", inner)[-1].strip()
    return field if 0 < len(field) <= 30 else None


def short_cite_year_style(card: Card) -> str | None:
    cm = card.cite.marks["cite13"]
    short = "".join(ch for ch, v in zip(card.cite.text, cm) if v).strip()
    if not short:
        return None
    if re.search(r"[’']\d{2}\b", short):
        return "’YY"
    if re.search(r"\b(19|20)\d{2}\b", short):
        return "YYYY"
    if re.search(r"\b\d{2}\b", short):
        return "YY"
    return "no year"


def compute_stats(cards: list[Card], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    by_file: dict[str, list[Card]] = defaultdict(list)
    for c in cards:
        by_file[c.meta["file"]].append(c)

    def summarize(cs: list[Card]) -> dict[str, Any]:
        hl_dens, ul_dens, hl_of_ul, em_of_ul, words = [], [], [], [], []
        colors: Counter[str] = Counter()
        unread_size: Counter[str] = Counter()
        hl_out_ul = em_out_ul = 0
        cards_hl_out = cards_em_out = 0
        no_highlight = no_underline = 0
        partial_cards = 0
        partial_kinds: Counter[str] = Counter()
        partial_examples: list[str] = []
        partial_total = 0
        hl_words_total = 0
        ul_partial_cards = 0
        for c in cs:
            b = c.body
            ul = b.marks["underline"]
            hl = b.marks["highlight"]
            em = b.marks["emphasis"]
            n = _nonws(b, lambda i: True)
            u = _nonws(b, lambda i: ul[i])
            h = _nonws(b, lambda i: hl[i] is not None)
            e = _nonws(b, lambda i: em[i])
            words.append(len(tokenize(b.text)))
            if not u:
                no_underline += 1
            if not h:
                no_highlight += 1
            if n:
                hl_dens.append(h / n)
                ul_dens.append(u / n)
            if u:
                hl_of_ul.append(h / u)
                em_of_ul.append(e / u)
            ho = _nonws(b, lambda i: hl[i] is not None and not ul[i])
            eo = _nonws(b, lambda i: em[i] and not ul[i])
            hl_out_ul += ho
            em_out_ul += eo
            cards_hl_out += bool(ho)
            cards_em_out += bool(eo)
            for i, ch in enumerate(b.text):
                if ch.isspace():
                    continue
                if hl[i] is not None:
                    colors[hl[i]] += 1
                if not ul[i]:
                    sz = b.marks["size"][i]
                    unread_size[f"{sz / 2:g}pt" if sz else "default"] += 1
            pw = partial_words(b, "highlight")
            hl_words_total += sum(
                1 for m in WORD_CORE.finditer(b.text)
                if any(hl[i] is not None for i in range(m.start(), m.end()))
            )
            if pw:
                partial_cards += 1
                partial_total += len(pw)
                for p in pw:
                    partial_kinds[p["kind"]] += 1
                    if len(partial_examples) < 40 and p["shown"] not in partial_examples:
                        partial_examples.append(p["shown"])
            if partial_words(b, "underline"):
                ul_partial_cards += 1
        total_hl_chars = sum(colors.values()) or 1
        total_unread = sum(unread_size.values()) or 1
        return {
            "cards": len(cs),
            "body_words": _dist(words),
            "underline_of_total": _dist(ul_dens),
            "highlight_of_total": _dist(hl_dens),
            "highlight_of_underline": _dist(hl_of_ul),
            "emphasis_of_underline": _dist(em_of_ul),
            "cards_without_underline": no_underline,
            "cards_without_highlight": no_highlight,
            "highlight_colors_pct": {k: round(100 * v / total_hl_chars, 2) for k, v in colors.most_common()},
            "unread_text_size_pct": {k: round(100 * v / total_unread, 1) for k, v in unread_size.most_common(8)},
            "nesting": {
                "highlight_chars_outside_underline": hl_out_ul,
                "cards_with_highlight_outside_underline": cards_hl_out,
                "emphasis_chars_outside_underline": em_out_ul,
                "cards_with_emphasis_outside_underline": cards_em_out,
            },
            "partial_word_highlight": {
                "cards_with_any": partial_cards,
                "pct_cards": round(100 * partial_cards / max(1, len(cs)), 1),
                "partial_words": partial_total,
                "pct_of_highlighted_words": round(100 * partial_total / max(1, hl_words_total), 2),
                "kinds": dict(partial_kinds),
                "examples": partial_examples,
            },
            "cards_with_partial_word_underline": ul_partial_cards,
        }

    cite_formats: dict[str, Counter[str]] = defaultdict(Counter)
    year_styles: Counter[str] = Counter()
    cutters: Counter[str] = Counter()
    tag_words, tag_ul, tag_dash = [], 0, 0
    for c in cards:
        fmt = cite_format(c)
        cite_formats[c.meta["file"]][fmt] += 1
        cite_formats["ALL"][fmt] += 1
        if (ys := short_cite_year_style(c)):
            year_styles[ys] += 1
        if fmt == "bracketed" and (who := bracket_cutter(c)):
            cutters[who] += 1
        tag_words.append(len(tokenize(c.tag.text)))
        tag_ul += any(c.tag.marks["underline"])
        tag_dash += "---" in c.tag.text or "—" in c.tag.text

    stats: dict[str, Any] = {
        "overall": summarize(cards),
        "by_file": {f: summarize(cs) for f, cs in sorted(by_file.items())},
        "cite_formats": {k: dict(v) for k, v in cite_formats.items()},
        "short_cite_year_style": dict(year_styles),
        "bracket_cite_cutters": dict(cutters.most_common(10)),
        "tags": {
            "words": _dist(tag_words),
            "pct_with_underline": round(100 * tag_ul / max(1, len(cards)), 1),
            "pct_with_dash": round(100 * tag_dash / max(1, len(cards)), 1),
        },
    }
    if extra:
        stats["parse"] = extra
    return stats


def _pct(d: dict[str, float], key: str) -> str:
    return f"{100 * d[key]:.1f}%" if key in d else "-"


def stats_markdown(s: dict[str, Any]) -> str:
    o = s["overall"]
    lines = ["# Corpus stats", ""]
    if "parse" in s:
        p = s["parse"]
        lines += [
            f"- Files: {p['files']}  ·  cards: **{o['cards']}**  ·  analytics: {p['analytics']}  ·  "
            f"tags with no card: {p['tag_only']}  ·  cards without a styled cite: {p['no_cite13']}",
            "",
        ]
    lines += ["## Density (non-whitespace characters, per card)", "",
              "| | p10 | p25 | median | p75 | p90 | mean |", "|---|---|---|---|---|---|---|"]
    for label, key in [
        ("underline / total", "underline_of_total"),
        ("highlight / total", "highlight_of_total"),
        ("highlight / underline", "highlight_of_underline"),
        ("emphasis / underline", "emphasis_of_underline"),
    ]:
        d = o[key]
        lines.append(f"| {label} | " + " | ".join(_pct(d, k) for k in ("p10", "p25", "median", "p75", "p90", "mean")) + " |")
    bw = o["body_words"]
    lines += ["", f"Body length (words): median {bw['median']:.0f}, p10 {bw['p10']:.0f}, p90 {bw['p90']:.0f}.", ""]

    lines += ["## By file", "", "| file | cards | highlight/total (median) | highlight/underline (median) | underline/total (median) | highlight colors | unread text size |", "|---|---|---|---|---|---|---|"]
    for f, d in s["by_file"].items():
        cols = ", ".join(f"{k} {v}%" for k, v in list(d["highlight_colors_pct"].items())[:3])
        sizes = ", ".join(f"{k} {v}%" for k, v in list(d["unread_text_size_pct"].items())[:3])
        lines.append(
            f"| {f} | {d['cards']} | {_pct(d['highlight_of_total'], 'median')} | {_pct(d['highlight_of_underline'], 'median')} | "
            f"{_pct(d['underline_of_total'], 'median')} | {cols} | {sizes} |"
        )

    pw = o["partial_word_highlight"]
    lines += ["", "## Partial-word highlighting", "",
              f"- {pw['cards_with_any']} cards ({pw['pct_cards']}%) have at least one partly highlighted word; "
              f"{pw['partial_words']} words total, {pw['pct_of_highlighted_words']}% of highlighted words.",
              f"- Kinds: {pw['kinds']} (prefix = **biod**iversity, initial = **i**mpact).",
              f"- Examples (CAPS = highlighted): {', '.join(pw['examples'][:25])}", ""]

    n = o["nesting"]
    lines += ["## Nesting (is highlight/emphasis always inside underline?)", "",
              f"- Highlight outside underline: {n['highlight_chars_outside_underline']} chars in {n['cards_with_highlight_outside_underline']} cards.",
              f"- Emphasis outside underline: {n['emphasis_chars_outside_underline']} chars in {n['cards_with_emphasis_outside_underline']} cards.",
              f"- Cards with no highlighting at all: {o['cards_without_highlight']}; no underline: {o['cards_without_underline']}.", ""]

    lines += ["## Cites", "", "| file | " + " | ".join(["bracketed", "inline", "unstyled", "none"]) + " |", "|---|---|---|---|---|"]
    for f, d in s["cite_formats"].items():
        lines.append(f"| {f} | " + " | ".join(str(d.get(k, 0)) for k in ["bracketed", "inline", "unstyled", "none"]) + " |")
    lines += ["", f"- Short-cite year style: {s['short_cite_year_style']}",
              f"- Last field of bracketed cites (cutter): {s['bracket_cite_cutters']}", ""]

    t = s["tags"]
    lines += ["## Tags", "",
              f"- Words: median {t['words']['median']:.0f}, p10 {t['words']['p10']:.0f}, p90 {t['words']['p90']:.0f}.",
              f"- {t['pct_with_underline']}% of tags underline some words; {t['pct_with_dash']}% use a dash (`---` or —).", ""]
    return "\n".join(lines)
