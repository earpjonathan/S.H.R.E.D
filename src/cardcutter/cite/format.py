"""Cite strings in the two formats the example files use.

inline:
    Dr. Samuel Charap 26, PhD, Distinguished Chair ..., "Title," Foreign Affairs, 04/07/2026, https://...
bracketed:
    Levine ’11 [Evan; September 16; B.A. in Astronomy ...; Journal of Risk Research, “Title,” https://..., <cutter>]

The short cite (last name + year) gets the Style13ptBold run. A field we don't know is
never invented: it is written as a visible marker such as "[quals?]" and reported.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from cardcutter.card import FText
from cardcutter.cite.meta import MONTHS, CiteMeta, parse_date

HONORIFICS = re.compile(r"^(dr|prof|professor|mr|ms|mrs|sir)\.?\s+", re.I)
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "ph.d."}


@dataclass
class CiteResult:
    cite: FText
    missing: list[str] = field(default_factory=list)


def split_name(full: str) -> tuple[str, str]:
    """('Thomas E.', 'Rotnem') from 'Dr. Thomas E. Rotnem'. Handles 'Last, First'."""
    name = HONORIFICS.sub("", full.strip())
    if "," in name and name.split(",", 1)[1].strip().lower() not in SUFFIXES:
        last, first = [p.strip() for p in name.split(",", 1)]
        return first, last
    parts = [p for p in name.split() if p.lower().strip(",") not in SUFFIXES]
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]


def _join(pieces: list[str]) -> str:
    """', '-join, except right after a quoted title that already ends in a comma ("Title," Pub)."""
    out = ""
    for p in pieces:
        if not p:
            continue
        if out:
            out += " " if out.endswith((',"', ",”")) else ", "
        out += p
    return out


def _yy(year: int | None) -> str:
    return f"{year % 100:02d}" if year else ""


# Bylines that name a desk or a label rather than a person or an organization.
GENERIC_AUTHOR = re.compile(r"\b(staff|editorial|editors?|team|desk|newsroom|admin(istrator)?|contributors?|"
                            r"correspondents?|reporters?|wire|paper|board|anonymous|unknown|guest)\b", re.I)
ORG_AUTHOR = re.compile(r"\b(institute|institution|center|centre|council|foundation|university|agency|ministry|"
                        r"department|office|corporation|association|organi[sz]ation|commission|committee|"
                        r"group|service|program(me)?|project|forum|society|bank|fund)\b", re.I)


_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def _visible(meta: CiteMeta) -> CiteMeta:
    """Drop zero-width characters and soft hyphens from every text field: PDFs put them inside
    names ("Alex\u200bander Velez-Green")."""
    import dataclasses

    changes = {}
    for f in dataclasses.fields(meta):
        v = getattr(meta, f.name)
        if isinstance(v, str) and _INVISIBLE.search(v):
            changes[f.name] = _INVISIBLE.sub("", v)
        elif isinstance(v, list) and any(isinstance(x, str) and _INVISIBLE.search(x) for x in v):
            changes[f.name] = [_INVISIBLE.sub("", x) if isinstance(x, str) else x for x in v]
    return dataclasses.replace(meta, **changes) if changes else meta


def build_cite(meta: CiteMeta, fmt: str = "inline", cutter: str = "", marker: str = "[{field}?]") -> CiteResult:
    meta = _visible(meta)
    missing: list[str] = []

    def val(name: str, value: str) -> str:
        if value:
            return value
        missing.append(name)
        return marker.format(field=name)

    d = parse_date(meta.date)
    author = meta.authors[0] if meta.authors else ""
    if author and GENERIC_AUTHOR.search(author):  # "Editorial paper", "U S News Staff": cite the outlet
        author = ""
    if author and ORG_AUTHOR.search(author):       # "Lansing Institute" is one name, not given + surname
        given, last = "", author
    else:
        given, last = split_name(author) if author else ("", "")
    if not last:
        last = meta.publication or ""
    last = val("author", last)
    yy = _yy(d.year)
    if not yy:
        missing.append("date")
        yy = "??"
    quals = list(meta.quals) + [""] * max(0, len(meta.authors) - len(meta.quals))  # one slot per author
    many = len(meta.authors) > 3

    ft = FText("")
    if fmt == "bracketed":
        short = f"{last} ’{yy}"
        ft.append(short, {"cite13": True})
        month_day = f"{MONTHS[d.month - 1]} {d.day}" if d.month and d.day else (MONTHS[d.month - 1] if d.month else "")
        fields = [
            given,
            val("date", month_day),
            "; ".join(q for q in quals if q) if any(quals) else (val("quals", "") if meta.authors else ""),
            _join([
                val("publication", meta.publication),
                f"“{val('title', meta.title)},”",
                val("url", meta.url),
                cutter,
            ]),
        ]
        ft.append(" [" + "; ".join(f for f in fields if f) + "]", {})
    else:
        m = HONORIFICS.match(author.strip())
        lead = m.group(0) if m else ""
        if lead or given:
            ft.append(f"{lead}{given} ", {})
        ft.append(f"{last}{' et al.' if many else ''} {yy}", {"cite13": True})
        pieces: list[str] = []
        if quals and quals[0]:
            pieces.append(quals[0])
        elif meta.authors:   # an organization cited as the author ("Congressional Research Service 26") has no quals
            pieces.append(val("quals", ""))
        for k, other in enumerate(meta.authors[1:], 1):
            pieces.append(other)
            if k < 3 and k < len(quals) and quals[k]:  # quals for the first three authors
                pieces.append(quals[k])
        pieces.append(f"\"{val('title', meta.title)},\"")
        pieces.append(val("publication", meta.publication))
        if meta.volume:
            pieces.append(f"Vol {meta.volume}")
        if meta.issue:
            pieces.append(f"No {meta.issue}")
        if d.year and d.month and d.day:
            pieces.append(f"{d.month:02d}/{d.day:02d}/{d.year}")
        elif d.year:
            pieces.append(str(d.year))
        else:
            pieces.append(val("date", ""))
        pieces.append(val("url", meta.url))
        ft.append(", " + _join(pieces), {})
    return CiteResult(cite=ft, missing=list(dict.fromkeys(missing)))
