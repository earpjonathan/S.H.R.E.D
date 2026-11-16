"""Turn pasted text, a PDF, or a URL into clean paragraphs plus citation metadata.

"Clean" only touches layout, never wording: line breaks inside a paragraph become
spaces, a hyphen split across a line break is rejoined, runs of spaces collapse, and
repeated page headers/footers in PDFs are dropped; typesetting ligatures (ﬁ, ﬂ) become
plain letters. The card is later verified against this cleaned text, character for character.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from cardcutter.cite.meta import CiteMeta, find_doi

SENT_END = re.compile(r"[.!?…:][\"'”’)\]]*$")


@dataclass
class Document:
    paragraphs: list[str]
    meta: CiteMeta = field(default_factory=CiteMeta)
    kind: str = "text"
    origin: str = ""
    flags: list[str] = field(default_factory=list)   # e.g. "sponsored" for advertiser content
    byline: str = ""        # author boxes / bios on the page (not article text), for finding quals
    author_pages: list[str] = field(default_factory=list)  # author profile URLs found on the page

    @property
    def text(self) -> str:
        return "\n".join(self.paragraphs)

    def para_spans(self) -> list[tuple[int, int]]:
        spans, pos = [], 0
        for p in self.paragraphs:
            spans.append((pos, pos + len(p)))
            pos += len(p) + 1
        return spans

    @property
    def words(self) -> int:
        return sum(len(p.split()) for p in self.paragraphs)


LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})


def clean_paragraph(raw: str) -> str:
    s = raw.replace("\r", "").translate(LIGATURES)  # PDF typesetting ligatures are the same letters
    s = re.sub(r"(\w)\xad\s*(\w)", r"\1\2", s).replace("\xad", "")  # soft hyphens: "vulner\xad ability"
    s = re.sub(r"(\w)-\n(\w)", lambda m: m.group(1) + m.group(2) if m.group(2).islower() else m.group(0).replace("\n", ""), s)
    s = re.sub(r"[ \t]*\n[ \t]*", " ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def from_text(text: str, origin: str = "pasted text") -> Document:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"\n\s*\n", text):
        blocks = re.split(r"\n\s*\n", text)
    else:
        blocks = text.split("\n")
    paras = [clean_paragraph(b) for b in blocks]
    return Document([p for p in paras if p], CiteMeta(sources=["text"]), "text", origin)


def from_pdf(path: Path) -> Document:
    import pymupdf

    doc = pymupdf.open(str(path))
    pages: list[list[str]] = []
    for page in doc:
        blocks = [b for b in page.get_text("blocks", sort=True) if b[6] == 0]
        pages.append([b[4] for b in blocks if b[4].strip()])
    # drop lines that repeat on many pages (running heads, footers) and bare page numbers
    # Only short blocks (<= 12 words) can be running heads, so body text is never dropped.
    def head_key(block: str) -> str | None:
        line = block.strip().split("\n")[0]
        return re.sub(r"\d+", "#", line) if len(block.split()) <= 12 else None

    first_lines = Counter(k for pg in pages for b in pg if (k := head_key(b)))
    repeated = {k for k, v in first_lines.items() if len(pages) >= 3 and v >= max(3, len(pages) // 2)}
    paras: list[str] = []
    for pg in pages:
        for b in pg:
            if head_key(b) in repeated or re.fullmatch(r"\s*\d{1,4}\s*", b):
                continue
            p = clean_paragraph(b)
            if not p:
                continue
            # a block that ends mid-sentence continues in the next one (columns, page breaks)
            if paras and not SENT_END.search(paras[-1]) and p[:1].islower():
                paras[-1] = paras[-1] + " " + p
            else:
                paras.append(p)
    md = doc.metadata or {}
    front = " ".join(paras[:40])
    meta = CiteMeta(
        authors=[a.strip() for a in re.split(r";|, and | and ", md.get("author") or "") if a.strip()],
        title=(md.get("title") or "").strip(),
        date=_pdf_date(md.get("creationDate") or ""),
        doi=find_doi(front),
        sources=["pdf metadata"],
    )
    return Document(paras, meta, "pdf", str(path))


def _pdf_date(raw: str) -> str:
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", raw)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def from_html(html: str, url: str) -> Document:
    """Main article text and metadata from a downloaded HTML page."""
    import json

    import trafilatura

    raw = trafilatura.extract(html, url=url, output_format="json", with_metadata=True,
                              include_comments=False, include_tables=False, favor_precision=True)
    if not raw:
        raise ValueError(f"no article text found at {url}")
    data = json.loads(raw)
    paras = [clean_paragraph(p) for p in (data.get("text") or "").split("\n")]
    publication = data.get("sitename") or data.get("hostname") or ""
    meta = CiteMeta(
        authors=split_authors(data.get("author") or ""),
        date=data.get("date") or "",
        title=headline_title(strip_site_suffix(data.get("title") or "", publication), html, url),
        publication=publication,
        url=data.get("url") or url,
        doi=find_doi(" ".join(paras[:20])),
        sources=["trafilatura"],
    )
    if "." in publication and (org := ld_publisher(html)):   # "everycrsreport.com" -> "Congressional Research Service"
        meta.publication = org
    if (upd := updated_date(top_text(html))) and upd > (meta.date or ""):   # a report "Updated June 18, 2026"
        meta.date = upd
    scholarly = scholarly_meta(html)
    if scholarly is not None:  # journal pages: these tags are the publisher's own record, so they win
        meta = scholarly.merged(meta)
    flags = ["sponsored"] if is_sponsored(html, url) else []
    byline, pages = author_info(html, url, meta.authors)
    return Document([p for p in paras if p], meta, "url", url, flags, byline, pages)


_SPONSORED_PATH = re.compile(r"/(sponsored|sponsor-content|contributor-content|partner-content|paid-post|paidpost|"
                             r"brandvoice|brand-studio|advertorial|native-ad)s?(/|-|$)", re.I)


def is_sponsored(html: str, url: str) -> bool:
    """Advertiser-produced pages (branded content, paid posts, partner content). Their claims come from
    whoever paid for them, which a debater needs to know before reading them as independent evidence."""
    from urllib.parse import urlsplit

    if _SPONSORED_PATH.search(urlsplit(url).path):
        return True
    tags = dict(_meta_tags(html))
    if any(tags.get(k, "").lower() in ("true", "yes", "1") for k in ("brandedcontent", "sponsored", "is_sponsored")):
        return True
    return 'data-track-category="advertorial"' in html[:400_000]


_META_TAG = re.compile(r"<meta\s+[^>]*>", re.I)
_ATTR = re.compile(r"""(name|property|content)\s*=\s*("[^"]*"|'[^']*')""", re.I)


def _meta_tags(html: str) -> list[tuple[str, str]]:
    import html as htmllib

    out = []
    for tag in _META_TAG.findall(html[:400_000]):
        attrs = {k.lower(): htmllib.unescape(v[1:-1]).strip() for k, v in _ATTR.findall(tag)}
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        if key and attrs.get("content"):
            out.append((key, attrs["content"]))
    return out


def person_name(raw: str) -> str:
    """'Bennett, Joseph R.' -> 'Joseph R. Bennett'; names without a comma pass through."""
    s = " ".join(raw.split())
    if s.count(",") == 1:
        last, given = (x.strip() for x in s.split(","))
        if last and given and not re.fullmatch(r"(jr|sr|ii|iii|iv|phd|md)\.?", given, re.I):
            return f"{given} {last}"
    return s


# A byline piece that is a job label, not a name ("Kevin Holden Platt, Contributor").
_ROLE = re.compile(
    r"((senior|chief|staff|special|guest|opinion|contributing|freelance|foreign|political|national|"
    r"defen[cs]e|military|security|investigative|associate|deputy|executive|managing|news|global|"
    r"world|business|tech(nology)?|science)\s+)*"
    r"(contributor|writer|correspondent|reporter|editor|columnist|journalist|producer|staff)s?", re.I)


def split_authors(raw: str) -> list[str]:
    """An author string from page metadata -> full names.

    trafilatura turns 'Sandler, Ronald' into 'Sandler; Ronald' (and a list of such names into
    'Bennett; Joseph R; Maloney; Richard F'). When every other piece is a lone surname, the
    pieces are surname/given pairs; otherwise ';', ',' and 'and' separate whole names."""
    raw = re.sub(r"^\s*by\s+", "", raw, flags=re.I)
    raw = re.sub(r"\b(produced|sponsored|presented|paid for|brought to you)\s+by\b:?", "", raw, flags=re.I)
    pieces = [p.strip() for p in re.split(r";|, and | and |,", raw) if p.strip()]
    pieces = [p for p in pieces if not _ROLE.fullmatch(p)]
    surnames, givens = pieces[0::2], pieces[1::2]
    paired = (
        ";" in raw and "," not in raw and len(pieces) >= 2 and len(pieces) % 2 == 0
        and all(re.fullmatch(r"[A-Z][\w'’-]+", s) for s in surnames)
        # 'Sandler; Ronald', or a list where some given part has an initial ('Joseph R')
        and (len(pieces) == 2 or any(re.search(r"\b[A-Z]\.?$", g) for g in givens))
    )
    if paired:
        return [f"{g} {s}" for s, g in zip(surnames, givens)]
    return pieces


def scholarly_meta(html: str) -> CiteMeta | None:
    """Highwire/Google Scholar tags (citation_author, citation_journal_title, ...), which journal
    sites publish for indexers. None when the page has no citation_author tags."""
    tags = _meta_tags(html)
    get = lambda *names: next((v for k, v in tags if k in names), "")  # noqa: E731
    authors: list[str] = []
    insts: list[str] = []
    for k, v in tags:  # each citation_author is followed by its citation_author_institution tags
        if k == "citation_author":
            authors.append(person_name(v))
            insts.append("")
        elif k == "citation_author_institution" and authors and not insts[-1]:
            insts[-1] = clean_affiliation(v)
    if not authors:
        return None
    pairs = list(dict.fromkeys(zip(authors, insts)))
    quals = [q for _, q in pairs]
    return CiteMeta(
        authors=[a for a, _ in pairs],
        quals=quals if any(quals) else [],
        date=get("citation_publication_date", "citation_date", "citation_online_date").replace("/", "-"),
        title=get("citation_title"),
        publication=get("citation_journal_title", "citation_conference_title", "citation_publisher"),
        doi=re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", get("citation_doi"), flags=re.I),
        volume=get("citation_volume"),
        issue=get("citation_issue"),
        pages="-".join(x for x in (get("citation_firstpage"), get("citation_lastpage")) if x),
        sources=["citation meta tags"],
    )


def from_url(url: str, timeout: int = 30) -> Document:
    """Download a page (or PDF) and parse it. trafilatura's downloader first; some publishers
    (nature.com, for one) refuse it, so fall back to the same downloader auto mode uses."""
    import tempfile

    import trafilatura

    from urllib.parse import urlsplit

    looks_pdf = urlsplit(url).path.lower().endswith(".pdf")
    html = None if looks_pdf else trafilatura.fetch_url(url)
    first: Document | None = None
    if html and not html.lstrip().startswith("%PDF"):
        try:
            first = from_html(html, url)
        except ValueError:
            first = None
        if first is not None and first.words >= 150:
            return first
    # nothing, a PDF, or a stub (bot check, cookie wall): try the downloader auto mode uses
    from cardcutter.search.fetch import _download

    second: Document | None = None
    with tempfile.TemporaryDirectory() as tmp:
        try:
            body, kind, final = _download(url, Path(tmp), float(timeout))
            if kind == "application/pdf":
                pdf = Path(tmp) / "source.pdf"
                pdf.write_bytes(body)
                second = from_pdf(pdf)
                second.origin = final
                second.meta.url = second.meta.url or final
            else:
                second = from_html(body.decode("utf-8", errors="replace"), final)
        except Exception as e:
            if first is None:
                raise ValueError(f"could not download {url} ({type(e).__name__})") from e
    best = max((d for d in (first, second) if d is not None), key=lambda d: d.words)
    if best.words < 60:
        raise ValueError(f"could only read {best.words} words from {url}; the site may block automated "
                         "downloads. Save the page as a PDF or paste its text instead.")
    return best


_MONTHS = ("january february march april may june july august september october november "
           "december").split()
_UPDATED = re.compile(r"\bUpdated:?\s+(" + "|".join(m.capitalize() for m in _MONTHS) + r")\s+(\d{1,2}),\s+(\d{4})")


def top_text(html: str, chars: int = 3000) -> str:
    """The first visible text of the page body (headers included, scripts and styles not)."""
    body = re.split(r"<body\b", html, maxsplit=1, flags=re.I)[-1]
    body = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", body, flags=re.S | re.I)
    return " ".join(re.sub(r"<[^>]+>", " ", body).split())[:chars]


def updated_date(text: str) -> str:
    """ISO date of an "Updated June 18, 2026" line at the top of a report, else ""."""
    m = _UPDATED.search(text)
    if not m:
        return ""
    return f"{m.group(3)}-{_MONTHS.index(m.group(1).lower()) + 1:02d}-{int(m.group(2)):02d}"


def ld_publisher(html: str) -> str:
    """The publishing organization's name from the page's schema.org data, else ""."""
    import json

    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []:
            pub = item.get("publisher") if isinstance(item, dict) else None
            name = (pub.get("name") if isinstance(pub, dict) else "") or ""
            if 2 <= len(name.split()) <= 8 and "." not in name:
                return " ".join(name.split())
    return ""


def _slug_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def headline_title(title: str, html: str, url: str) -> str:
    """A page's <title>/og:title is sometimes an SEO line that isn't the article's title
    ("surviving nuclear war" for "Resilience and Resistance: ..."). When the page's <h1> matches
    the URL slug and the title doesn't, the <h1> is the title."""
    import html as htmllib
    from urllib.parse import urlparse

    parts = [p for p in urlparse(url).path.split("/") if p]
    slug = _slug_words(parts[-1].rsplit(".", 1)[0]) if parts else set()
    if len(slug) < 3:
        return title
    for m in re.finditer(r"<h1\b[^>]*>(.*?)</h1>", html, re.S | re.I):
        h1 = " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split())
        if not h1 or len(h1.split()) < 3:
            continue
        def score(t: str) -> float:
            return len(_slug_words(t) & slug) / len(slug)
        if score(h1) >= 0.6 and score(h1) > score(title) + 0.3:
            return h1
    return title


def strip_site_suffix(title: str, site: str) -> str:
    """'Marine protected area - Wikipedia' -> 'Marine protected area' when the suffix names the site."""
    m = re.match(r"^(.*\S)\s+[-|–—]\s+([^-|–—]{2,60})$", title.strip())
    if not m or not site:
        return title.strip()
    suffix = re.sub(r"[^a-z0-9]", "", m.group(2).lower())
    site_key = re.sub(r"[^a-z0-9]", "", site.lower().split(".")[0] if "." in site and " " not in site else site.lower())
    if suffix and site_key and (suffix in site_key or site_key in suffix):
        return m.group(1)
    return title.strip()


def load_source(arg: str) -> Document:
    """A URL, a .pdf path, or a text file path."""
    if re.match(r"https?://", arg):
        return from_url(arg)
    path = Path(arg).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"{arg} is not a URL or an existing file (use --text for pasted text)")
    if path.suffix.lower() == ".pdf":
        return from_pdf(path)
    return from_text(path.read_text(encoding="utf-8", errors="replace"), origin=str(path))


_ORG = re.compile(r"\b(universit|institut|college|department|dept|school|faculty|centre|center|laborator|lab\b|agency|"
                  r"office|ministry|council|foundation|museum|hospital|conservancy|society|group|program|service|"
                  r"survey|commission|organi[sz]ation|bank|fund|trust|academy|inc\b|llc|ltd|corporation|company|"
                  r"division|unit\b|station|observatory|garden|zoo\b|authority|association|network|initiative)", re.I)


def clean_affiliation(raw: str) -> str:
    """'Department of Biology, Carleton University, Ottawa, Canada' -> 'Department of Biology, Carleton
    University': keep the organisation parts, drop street address, city, postcode, and country."""
    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    last = max((i for i, p in enumerate(parts) if _ORG.search(p)), default=-1)
    kept = parts[:last + 1] if last >= 0 else parts[:1]
    return ", ".join(p for p in kept if not re.search(r"\d{3,}|^\d|\b(Box|Bag|Drive|Street|Road|Avenue)\b", p))


_AUTHOR_CLASS = re.compile(r"author|byline|bio\b|bio[-_]|contributor|writer", re.I)
_SOCIAL = re.compile(r"linkedin|twitter|x\.com|facebook|instagram|threads|bsky|mastodon|youtube|orcid", re.I)


def author_info(html: str, url: str, authors: list[str], max_chars: int = 1800) -> tuple[str, list[str]]:
    """Text about the authors that article extraction drops: author boxes, bylines, and
    schema.org author data (jobTitle, affiliation). Also author profile links. Page data only."""
    import json
    from urllib.parse import urljoin

    import lxml.html

    texts: list[str] = []
    pages: list[str] = []
    try:
        root = lxml.html.fromstring(html[:600_000])
    except Exception:
        return "", []
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag in ("script", "style", "noscript", "svg", "button", "nav"):
            continue
        marks = " ".join(el.get(a, "") for a in ("class", "itemprop", "rel", "id", "data-test"))
        if not _AUTHOR_CLASS.search(marks):
            continue
        t = " ".join(" ".join(el.xpath(".//text()[not(ancestor::script) and not(ancestor::style)]")).split())
        t = re.sub(r"\s*(View author publications|Search author on:?|PubMed|Google Scholar|Share on \w+)\s*", " ", t).strip()
        if 3 <= len(t.split()) <= 70 and re.search(r"[A-Za-z]{3}", t):
            texts.append(t)
        if el.tag == "a" and el.get("href") and ("author" in (el.get("rel") or "") or "author" in el.get("class", "")):
            pages.append(urljoin(url, el.get("href")))
    for block in root.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for person in _ld_people(data):
            bits = [person.get("jobTitle"), person.get("description")]
            for org_key in ("affiliation", "worksFor"):
                orgs = person.get(org_key) or []
                for org in orgs if isinstance(orgs, list) else [orgs]:
                    bits.append(org.get("name") if isinstance(org, dict) else org)
            bits = [" ".join(str(b).split()) for b in bits if isinstance(b, str) and b.strip()]
            if person.get("name") and bits:
                texts.append(f"{person['name']}: " + "; ".join(bits))
            u = person.get("url")
            if isinstance(u, str) and u.startswith("http"):
                pages.append(u)
    # keep the texts that mention an author or read like a role; longest-first, no duplicates
    surnames = {a.split()[-1].lower() for a in authors if a.split()}
    keep: list[str] = []
    for t in sorted(dict.fromkeys(texts), key=len, reverse=True):
        low = t.lower()
        if any(t in k for k in keep):
            continue
        if surnames and not any(n in low for n in surnames) and not re.search(
                r"professor|fellow|director|researcher|editor|reporter|correspondent|scientist|lecturer|analyst|"
                r"phd|ph\.d|university|institute", low):
            continue
        keep.append(t)
    out, used = [], 0
    for t in keep:
        if used + len(t) > max_chars:
            break
        out.append(t)
        used += len(t)
    pages = [p for p in dict.fromkeys(pages) if not _SOCIAL.search(p) and p.rstrip("/") != url.rstrip("/")]
    return "\n".join(out), pages[:3]


def _ld_people(data) -> list[dict]:
    """schema.org Person objects under any "author" key in a JSON-LD block."""
    found: list[dict] = []

    def walk(x, under_author=False):
        if isinstance(x, list):
            for y in x:
                walk(y, under_author)
        elif isinstance(x, dict):
            if under_author and x.get("name") and x.get("@type") in (None, "Person", ["Person"]):
                found.append(x)
            for k, v in x.items():
                walk(v, under_author or k == "author")
    walk(data)
    return found
