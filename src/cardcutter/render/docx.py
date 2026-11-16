"""Write cards as Verbatim-formatted Word paragraphs.

Each maximal run of identical formatting becomes one `w:r`:
- the character style is Style13ptBold (short cite), else Emphasis, else
  StyleUnderline, else none;
- direct formatting is added only where the wanted look differs from what that style
  already gives (for example `w:u w:val="none"` or a shrunk `w:sz`).

This mirrors `corpus.parse.run_format`, so parse(render(card)) == card.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any, Iterable

from lxml import etree

from cardcutter.card import LINE_BREAK, Card, FText
from cardcutter.ooxml import OFF_VALUES, W, XML_SPACE, RunProps, StyleSheet, iter_runs, p_style, paragraph_text

# CT_RPr child order (Word rejects out-of-order run properties).
RPR_ORDER = [
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
    "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing",
    "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
    "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
]

STYLE_NAMES = {
    "tag": "heading 4",
    "underline": "Style Underline",
    "emphasis": "Emphasis",
    "cite13": "Style 13 pt Bold",
    "undertag": "Undertag",
}

SPECIAL_CHARS = {"\t": "tab", LINE_BREAK: "br", "­": "softHyphen", "‑": "noBreakHyphen"}


def _el(tag: str, **attrs: str) -> etree._Element:
    e = etree.Element(f"{W}{tag}")
    for k, v in attrs.items():
        e.set(f"{W}{k}", v)
    return e


class DocxRenderer:
    def __init__(self, styles_xml: bytes):
        self.styles_xml = styles_xml
        self.sheet = StyleSheet.from_xml(styles_xml)
        self.ids: dict[str, str | None] = {k: self.sheet.id_for_name(n) for k, n in STYLE_NAMES.items()}
        missing = [STYLE_NAMES[k] for k in ("tag", "underline", "emphasis", "cite13") if not self.ids[k]]
        if missing:
            raise ValueError(f"styles.xml lacks Verbatim styles: {missing}")

    # -- runs -----------------------------------------------------------------------
    def _rpr(self, fmt: dict[str, Any], pstyle: str | None) -> etree._Element | None:
        # Tags underline with direct w:u (as the example files do), so underlined tag
        # words keep the heading's 13 pt instead of StyleUnderline's 11 pt.
        in_tag = pstyle is not None and pstyle == self.ids["tag"]
        if fmt.get("cite13"):
            rstyle = self.ids["cite13"]
        elif fmt.get("emphasis"):
            rstyle = self.ids["emphasis"]
        elif fmt.get("underline") and not in_tag:
            rstyle = self.ids["underline"]
        else:
            rstyle = None
        implied = self.sheet.effective(pstyle, rstyle, RunProps())
        implied_ul = bool(implied.u) and implied.u.lower() not in OFF_VALUES

        props: dict[str, etree._Element] = {}
        if rstyle:
            props["rStyle"] = _el("rStyle", val=rstyle)
        if fmt.get("bold"):
            props["b"] = _el("b") if fmt["bold"] == "on" else _el("b", val="0")
        if fmt.get("italic"):
            props["i"] = _el("i") if fmt["italic"] == "on" else _el("i", val="0")
        if fmt.get("strike"):
            props["strike"] = _el("strike")
        size = fmt.get("size")
        if size is not None and size != implied.sz:
            props["sz"] = _el("sz", val=str(size))
            props["szCs"] = _el("szCs", val=str(size))
        hl = fmt.get("highlight")
        if hl and not hl.startswith("shd:"):
            props["highlight"] = _el("highlight", val=hl)
        if bool(fmt.get("underline")) != implied_ul:
            props["u"] = _el("u", val="single" if fmt.get("underline") else "none")
        if fmt.get("emphasis") and rstyle != self.ids["emphasis"]:
            props["bdr"] = _el("bdr", val="single", sz="4", space="0", color="auto")
        if hl and hl.startswith("shd:"):
            props["shd"] = _el("shd", val="clear", color="auto", fill=hl[4:])
        if fmt.get("vert"):
            props["vertAlign"] = _el("vertAlign", val=fmt["vert"])
        if not props:
            return None
        rpr = _el("rPr")
        for name in RPR_ORDER:
            if name in props:
                rpr.append(props[name])
        return rpr

    def _run(self, text: str, fmt: dict[str, Any], pstyle: str | None) -> etree._Element:
        r = _el("r")
        rpr = self._rpr(fmt, pstyle)
        if rpr is not None:
            r.append(rpr)
        buf: list[str] = []

        def flush() -> None:
            if buf:
                t = _el("t")
                t.text = "".join(buf)
                t.set(XML_SPACE, "preserve")
                r.append(t)
                buf.clear()

        for ch in text:
            if ch in SPECIAL_CHARS:
                flush()
                r.append(_el(SPECIAL_CHARS[ch]))
            else:
                buf.append(ch)
        flush()
        return r

    def paragraph(self, ft: FText, pstyle_key: str | None = None) -> etree._Element:
        p = _el("p")
        pstyle = self.ids.get(pstyle_key) if pstyle_key else None
        if pstyle:
            ppr = _el("pPr")
            ppr.append(_el("pStyle", val=pstyle))
            p.append(ppr)
        for s, e, fmt in ft.runs():
            p.append(self._run(ft.text[s:e], fmt, pstyle))
        return p

    def card_paragraphs(self, card: Card) -> list[etree._Element]:
        out = [self.paragraph(card.tag, "tag")]
        if card.meta.get("undertag") and self.ids["undertag"]:
            out.append(self.paragraph(FText.from_json(card.meta["undertag"]), "undertag"))
        if card.cite.text:
            out.append(self.paragraph(card.cite))
        out.extend(self.paragraph(p) for p in card.body.paragraphs())
        return out


# -- writing files ------------------------------------------------------------------
def _write_zip(src: Path, dst: Path, replace: dict[str, bytes]) -> None:
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = replace.get(item.filename, zin.read(item.filename))
            zout.writestr(item, data)
    tmp.replace(dst)


def write_new(cards: Iterable[Card], template: Path, out_path: Path) -> Path:
    """A new .docx with the cards, using the template's styles and page setup."""
    with zipfile.ZipFile(template) as z:
        styles = z.read("word/styles.xml")
        doc = etree.fromstring(z.read("word/document.xml"))
    r = DocxRenderer(styles)
    body = doc.find(f"{W}body")
    sect = body.find(f"{W}sectPr")
    for card in cards:
        for p in r.card_paragraphs(card):
            if sect is not None:
                sect.addprevious(p)
            else:
                body.append(p)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_zip(template, out_path, {"word/document.xml": etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True)})
    return out_path


def _ensure_styles(target_styles: bytes, template_styles: bytes) -> bytes:
    """Copy any Verbatim style the target lacks from the template's styles.xml."""
    tgt = etree.fromstring(target_styles)
    tpl = etree.fromstring(template_styles)
    have = {(s.find(f"{W}name").get(f"{W}val") or "").lower() for s in tgt.iter(f"{W}style") if s.find(f"{W}name") is not None}
    ids = {s.get(f"{W}styleId") for s in tgt.iter(f"{W}style")}
    changed = False
    for s in tpl.iter(f"{W}style"):
        name_el = s.find(f"{W}name")
        name = (name_el.get(f"{W}val") if name_el is not None else "").lower()
        if name in {n.lower() for n in STYLE_NAMES.values()} and name not in have and s.get(f"{W}styleId") not in ids:
            tgt.append(s)
            changed = True
    return etree.tostring(tgt, xml_declaration=True, encoding="UTF-8", standalone=True) if changed else target_styles


def append_under_heading(
    cards: Iterable[Card], docx_path: Path, heading: str, template: Path, out_path: Path | None = None
) -> Path:
    """Insert cards at the end of the section under the first heading whose text matches.

    The section ends at the next heading of the same or a higher level. The original
    file is only overwritten if out_path is the same path; a .bak copy is kept then.
    """
    out_path = out_path or docx_path
    with zipfile.ZipFile(docx_path) as z:
        styles = z.read("word/styles.xml")
        doc = etree.fromstring(z.read("word/document.xml"))
    with zipfile.ZipFile(template) as z:
        tpl_styles = z.read("word/styles.xml")
    styles = _ensure_styles(styles, tpl_styles)
    r = DocxRenderer(styles)
    body = doc.find(f"{W}body")
    levels = {f"heading {i}": i for i in range(1, 7)}

    paras = [el for el in body if el.tag == f"{W}p"]
    want = heading.strip().lower()
    start = level = None
    for idx, p in enumerate(paras):
        lvl = levels.get(r.sheet.name(p_style(p)))
        if lvl and paragraph_text(p).strip().lower() == want:
            start, level = idx, lvl
            break
    if start is None:
        raise KeyError(f"no heading with text {heading!r} in {docx_path.name}")
    anchor = paras[start]
    for p in paras[start + 1:]:
        lvl = levels.get(r.sheet.name(p_style(p)))
        if lvl and lvl <= level:
            break
        anchor = p
    for card in cards:
        for new_p in r.card_paragraphs(card):
            anchor.addnext(new_p)
            anchor = new_p

    if out_path.resolve() == docx_path.resolve():
        shutil.copy2(docx_path, docx_path.with_suffix(".docx.bak"))
    _write_zip(docx_path, out_path, {
        "word/document.xml": etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True),
        "word/styles.xml": styles,
    })
    return out_path


__all__ = ["DocxRenderer", "write_new", "append_under_heading", "iter_runs"]
