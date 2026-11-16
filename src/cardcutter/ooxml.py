"""Shared WordprocessingML helpers: style resolution, run text, run properties.

Both the corpus parser and the renderer use the same rules, so a card that is
rendered and parsed again comes back identical.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
NSMAP = {"w": W_NS}

OFF_VALUES = {"0", "false", "off", "none", "nil"}

# Containers inside a paragraph whose runs are part of the paragraph's visible text.
RUN_CONTAINERS = {f"{W}{t}" for t in ("hyperlink", "ins", "smartTag", "fldSimple", "customXml", "moveTo", "bdo", "dir")}
SKIP_CONTAINERS = {f"{W}{t}" for t in ("del", "moveFrom")}


def val(el: etree._Element | None, attr: str = "val") -> str | None:
    if el is None:
        return None
    return el.get(f"{W}{attr}")


def on_off(el: etree._Element | None) -> bool | None:
    """Toggle property: None if absent, else True unless w:val says off."""
    if el is None:
        return None
    v = el.get(f"{W}val")
    return v is None or v.lower() not in OFF_VALUES


@dataclass
class RunProps:
    """The run properties CardCutter cares about. None = not specified at this level."""

    u: str | None = None          # underline type; "none" means explicitly off
    bdr: bool | None = None       # box border (Verbatim emphasis)
    b: bool | None = None
    i: bool | None = None
    strike: bool | None = None
    sz: int | None = None         # half-points
    highlight: str | None = None  # Word highlight name, "none" = explicitly off
    shd_fill: str | None = None   # shading fill hex
    vert: str | None = None       # superscript / subscript / baseline

    @classmethod
    def from_rpr(cls, rpr: etree._Element | None) -> "RunProps":
        if rpr is None:
            return cls()
        p = cls()
        p.u = val(rpr.find(f"{W}u"))
        bdr = rpr.find(f"{W}bdr")
        if bdr is not None:
            p.bdr = (val(bdr) or "single").lower() not in OFF_VALUES
        p.b = on_off(rpr.find(f"{W}b"))
        p.i = on_off(rpr.find(f"{W}i"))
        p.strike = on_off(rpr.find(f"{W}strike"))
        sz = val(rpr.find(f"{W}sz"))
        p.sz = int(float(sz)) if sz else None
        p.highlight = val(rpr.find(f"{W}highlight"))
        shd = rpr.find(f"{W}shd")
        if shd is not None:
            p.shd_fill = val(shd, "fill")
        p.vert = val(rpr.find(f"{W}vertAlign"))
        return p

    def over(self, base: "RunProps") -> "RunProps":
        """self overrides base wherever self specifies a value."""
        return RunProps(**{k: (v if v is not None else getattr(base, k)) for k, v in self.__dict__.items()})


@dataclass
class Style:
    style_id: str
    type: str
    name: str
    based_on: str | None
    rpr: RunProps


@dataclass
class StyleSheet:
    styles: dict[str, Style] = field(default_factory=dict)
    defaults: RunProps = field(default_factory=RunProps)
    default_para: str | None = None
    _cache: dict[str, RunProps] = field(default_factory=dict, repr=False)

    @classmethod
    def from_xml(cls, xml: bytes) -> "StyleSheet":
        root = etree.fromstring(xml)
        sheet = cls()
        d = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
        sheet.defaults = RunProps.from_rpr(d)
        for s in root.iter(f"{W}style"):
            sid = s.get(f"{W}styleId")
            if not sid:
                continue
            name_el = s.find(f"{W}name")
            sheet.styles[sid] = Style(
                style_id=sid,
                type=s.get(f"{W}type") or "",
                name=(val(name_el) or sid),
                based_on=val(s.find(f"{W}basedOn")),
                rpr=RunProps.from_rpr(s.find(f"{W}rPr")),
            )
            if s.get(f"{W}default") in ("1", "true") and s.get(f"{W}type") == "paragraph":
                sheet.default_para = sid
        return sheet

    def resolved(self, style_id: str | None) -> RunProps:
        """Run properties of a style with its basedOn chain applied (no docDefaults)."""
        if not style_id or style_id not in self.styles:
            return RunProps()
        if style_id in self._cache:
            return self._cache[style_id]
        seen: list[Style] = []
        sid: str | None = style_id
        while sid and sid in self.styles and all(s.style_id != sid for s in seen):
            seen.append(self.styles[sid])
            sid = self.styles[sid].based_on
        props = RunProps()
        for s in reversed(seen):
            props = s.rpr.over(props)
        self._cache[style_id] = props
        return props

    def name(self, style_id: str | None) -> str:
        if not style_id:
            return ""
        s = self.styles.get(style_id)
        return (s.name if s else style_id).lower()

    def effective(self, p_style: str | None, r_style: str | None, direct: RunProps) -> RunProps:
        """docDefaults <- paragraph style <- character style <- direct formatting.

        Toggle properties (bold/italic) are treated as plain overrides, which is exact
        for every case CardCutter stores (it keeps bold/italic from direct formatting).
        """
        props = self.defaults
        props = self.resolved(p_style or self.default_para).over(props)
        props = self.resolved(r_style).over(props)
        return direct.over(props)

    def id_for_name(self, name: str) -> str | None:
        for sid, s in self.styles.items():
            if s.name.lower() == name.lower():
                return sid
        return None


def iter_runs(p: etree._Element) -> Iterator[etree._Element]:
    """Runs that make up a paragraph's visible text, in order."""
    for child in p:
        if child.tag == f"{W}r":
            yield child
        elif child.tag in SKIP_CONTAINERS:
            continue
        elif child.tag in RUN_CONTAINERS:
            yield from iter_runs(child)
        elif child.tag == f"{W}sdt":
            content = child.find(f"{W}sdtContent")
            if content is not None:
                yield from iter_runs(content)


def run_text(r: etree._Element) -> str:
    out: list[str] = []
    for c in r:
        tag = c.tag
        if tag == f"{W}t":
            out.append(c.text or "")
        elif tag == f"{W}tab":
            out.append("\t")
        elif tag in (f"{W}br", f"{W}cr"):
            out.append(" ")
        elif tag == f"{W}softHyphen":
            out.append("­")
        elif tag == f"{W}noBreakHyphen":
            out.append("‑")
        elif tag == f"{W}sym":
            ch = val(c, "char")
            if ch:
                try:
                    out.append(chr(int(ch, 16)))
                except ValueError:
                    pass
    return "".join(out)


def p_style(p: etree._Element) -> str | None:
    return val(p.find(f"{W}pPr/{W}pStyle"))


def r_style(r: etree._Element) -> str | None:
    return val(r.find(f"{W}rPr/{W}rStyle"))


def paragraph_text(p: etree._Element) -> str:
    return "".join(run_text(r) for r in iter_runs(p))

