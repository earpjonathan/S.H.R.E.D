"""Citation metadata, date parsing, and Crossref lookup for DOIs."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+[^\s\"<>,;.)])")


@dataclass
class CiteMeta:
    authors: list[str] = field(default_factory=list)   # full names, first author first
    quals: list[str] = field(default_factory=list)     # one per author where known
    date: str = ""                                      # as found
    title: str = ""
    publication: str = ""
    url: str = ""
    doi: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    sources: list[str] = field(default_factory=list)   # where each field came from

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def merged(self, other: "CiteMeta | dict[str, Any]") -> "CiteMeta":
        """Fill empty fields from `other` (manual overrides should be merged last with override())."""
        o = other if isinstance(other, CiteMeta) else CiteMeta(**{k: v for k, v in other.items() if k in CiteMeta.__dataclass_fields__})
        out = CiteMeta(**asdict(self))
        for k in CiteMeta.__dataclass_fields__:
            if k == "sources":
                continue
            if not getattr(out, k) and getattr(o, k):
                setattr(out, k, getattr(o, k))
        out.sources = self.sources + o.sources
        return out

    def override(self, **fields: Any) -> "CiteMeta":
        out = CiteMeta(**asdict(self))
        for k, v in fields.items():
            if v not in (None, "", []):
                setattr(out, k, v)
        if any(v not in (None, "", []) for v in fields.values()):
            out.sources = out.sources + ["manual"]
        return out


@dataclass(frozen=True)
class ParsedDate:
    year: int | None
    month: int | None = None
    day: int | None = None


def parse_date(raw: str) -> ParsedDate:
    s = (raw or "").strip()
    if not s:
        return ParsedDate(None)
    m = re.match(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", s)
    if m:
        return ParsedDate(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return ParsedDate(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    month = next((i + 1 for i, name in enumerate(MONTHS) if re.search(rf"\b{name[:3]}", s, re.I)), None)
    year_m = re.search(r"\b(19|20)\d{2}\b", s)
    day_m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b(?![\d/])", re.sub(r"\b(19|20)\d{2}\b", "", s))
    return ParsedDate(int(year_m.group(0)) if year_m else None, month, int(day_m.group(1)) if (day_m and month) else None)


def find_doi(text: str) -> str:
    m = DOI_RE.search(text or "")
    return m.group(1) if m else ""


def crossref(doi: str, timeout: float = 15.0) -> CiteMeta:
    """Look up a DOI on Crossref. Sends only the DOI (no personal details)."""
    r = httpx.get(f"https://api.crossref.org/works/{doi}", timeout=timeout,
                  headers={"User-Agent": "CardCutter/0.1 (citation lookup)"})
    r.raise_for_status()
    w = r.json()["message"]
    authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in w.get("author", [])]
    parts = (w.get("published-print") or w.get("published-online") or w.get("issued") or {}).get("date-parts", [[None]])[0]
    date = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts) if p) if parts and parts[0] else ""
    return CiteMeta(
        authors=[a for a in authors if a],
        date=date,
        title=(w.get("title") or [""])[0],
        publication=(w.get("container-title") or [""])[0],
        url=f"https://doi.org/{doi}",
        doi=doi,
        volume=str(w.get("volume") or ""),
        issue=str(w.get("issue") or ""),
        pages=str(w.get("page") or ""),
        sources=["crossref"],
    )


def openalex_affiliations(doi: str, timeout: float = 15.0) -> list[str]:
    """Each author's affiliation (as the publisher listed it) from OpenAlex, in author order.
    Sends only the DOI."""
    from cardcutter.ingest.source import clean_affiliation

    r = httpx.get(f"https://api.openalex.org/works/doi:{doi}", params={"select": "authorships"}, timeout=timeout,
                  headers={"User-Agent": "CardCutter/0.1 (citation lookup)"})
    r.raise_for_status()
    out = []
    for a in r.json().get("authorships", []):
        raw = (a.get("raw_affiliation_strings") or [""])[0]
        inst = ((a.get("institutions") or [{}])[0] or {}).get("display_name", "")
        out.append(clean_affiliation(raw) if raw else inst)
    return out
