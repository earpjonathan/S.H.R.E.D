"""Fill citation metadata: Crossref for DOIs, then (optionally) the model copies author
names and qualifications from the byline / author note. Anything the model returns that
is not found word for word in the article is dropped, so quals are never invented."""
from __future__ import annotations

import json
import re

from cardcutter.cite.meta import CiteMeta, crossref, openalex_affiliations
from cardcutter.ingest.source import Document
from cardcutter.llm import prompts, schemas
from cardcutter.llm.registry import Registry


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _in_text(fragment: str, haystack: str) -> bool:
    f = _norm(fragment)
    return bool(f) and f in haystack


def resolve_meta(doc: Document, reg: Registry | None, *, use_crossref: bool = True, extract_quals: bool = True,
                 progress=None) -> tuple[CiteMeta, list[str]]:
    notes: list[str] = []
    meta = doc.meta
    if use_crossref and meta.doi:
        try:
            cr = crossref(meta.doi)
            if doc.kind == "pdf":  # PDF metadata is often junk ("JEC PDF 2 ISSUE 4"): the DOI record wins
                meta = meta.override(**{k: getattr(cr, k) for k in ("title", "publication", "date", "authors")
                                        if getattr(cr, k)})
                meta.sources = [x for x in meta.sources if x != "manual"] + ["crossref"]
            meta = meta.merged(cr)
            notes.append(f"crossref: filled metadata for DOI {meta.doi}")
        except Exception as e:  # network or unknown DOI: keep going without it
            notes.append(f"crossref lookup failed for {meta.doi}: {type(e).__name__}")
    fallback: list[str] = []
    if use_crossref and meta.doi and not any(meta.quals):
        try:  # journal affiliations by DOI (only the DOI is sent)
            affs = openalex_affiliations(meta.doi)
            if any(affs) and len(affs) >= len(meta.authors[:3]):
                affs = affs[:len(meta.authors)] if meta.authors else affs
                # OpenAlex parses preprints' PDFs itself and can give every author one garbled string
                # ("Perry World House at the University of Pennsylvania. Paul"): read the paper first
                if meta.doi.lower().startswith("10.48550/") or (len(affs) >= 2 and len(set(affs)) == 1):
                    fallback = affs
                else:
                    meta.quals = affs
                    meta.sources = meta.sources + ["openalex affiliations"]
                    notes.append("quals: author affiliations from OpenAlex")
        except Exception as e:
            notes.append(f"openalex affiliation lookup failed: {type(e).__name__}")
    if extract_quals and reg is not None and doc.paragraphs and not any(meta.quals):
        if progress:
            progress("cite")
        byline = getattr(doc, "byline", "")
        if not byline.strip() or len(byline.split()) < 12:  # nothing useful on the page: try the author's profile
            byline = (byline + "\n" + author_page_text(getattr(doc, "author_pages", []))).strip()
        head = " ".join(doc.paragraphs[:6])[:2500]
        tail = " ".join(doc.paragraphs[-4:])[-1500:]
        excerpt = (f"About the authors (from the page):\n{byline}\n\n" if byline else "") + head \
            + ("\n…\n" + tail if len(doc.paragraphs) > 6 else "")
        known = json.dumps({k: v for k, v in meta.to_json().items() if v and k not in ("sources",)}, ensure_ascii=False)
        res = reg.call("quals", prompts.render("quals", excerpt=excerpt, known=known), schemas.QUALS, "quals")
        hay = _norm(excerpt)
        found = CiteMeta(
            authors=[a for a in res.data["authors"] if _in_text(a, hay)],
            quals=[q for q in res.data["quals"] if _in_text(q, hay)],
            date=res.data["date"] if _in_text(res.data["date"], hay) else "",
            publication=res.data["publication"] if _in_text(res.data["publication"], hay) else "",
            sources=["byline"],
        )
        dropped = (len(res.data["quals"]) - len(found.quals)) + (len(res.data["authors"]) - len(found.authors))
        if dropped:
            notes.append(f"dropped {dropped} model-suggested cite field(s) not found word for word in the article")
        names = meta.authors or found.authors
        raw = align_quals(names, res.data["authors"], res.data["quals"], hay)
        aligned = [tidy_qual(q, name=names[k] if k < len(names) else "") for k, q in enumerate(raw)]
        meta = meta.merged(found)
        meta.quals = aligned if any(aligned) else []
    if fallback and not any(meta.quals):
        meta.quals = fallback
        meta.sources = meta.sources + ["openalex affiliations"]
        notes.append("quals: author affiliations from OpenAlex (not found in the paper itself)")
    return meta, notes


def author_page_text(urls: list[str], timeout: float = 10.0, max_words: int = 160) -> str:
    """The first paragraphs of up to two author profile pages (a reporter or researcher bio)."""
    import trafilatura

    out = []
    for url in urls[:2]:
        try:
            html = trafilatura.fetch_url(url)
            text = trafilatura.extract(html, include_comments=False, include_tables=False) if html else None
        except Exception:
            continue
        if text:
            words = " ".join(text.split()).split()
            out.append(f"From the author's page {url}: " + " ".join(words[:max_words]))
    return "\n".join(out)


_BIO = re.compile(r"^(?:and\s+)?(?:is|was|has|had|his|her|their|who|whose|he|she|they|where|which|author of|"
                  r"the author|co-?author|writes|wrote|covers|covered|reporting|book|forthcoming|soon)\b", re.I)


_ABBREV = {"st", "dr", "mr", "ms", "mrs", "prof", "inc", "jr", "sr", "no", "vol", "gen", "col", "lt", "sgt",
           "adm", "rep", "sen", "gov", "amb", "capt", "maj", "co", "corp", "ltd", "dept", "univ", "u.s", "u.k"}


def _first_sentence(q: str) -> str:
    """A bio box runs on in full sentences ("... program. He has also studied ..."): keep the first."""
    for m in re.finditer(r"\.\s+(?=[A-Z])", q):
        before = re.search(r"([\w.]+)$", q[:m.start()])
        word = before.group(1).lower() if before else ""
        if len(word) <= 1 or word in _ABBREV or "." in word:   # "St. Andrews", "U.S. Army", "J. Smith"
            continue
        return q[:m.start()]
    return q


def tidy_qual(q: str, max_words: int = 30, name: str = "") -> str:
    """Keep a qual to titles and affiliations: drop the bio pieces a byline note runs on into
    ("is author of the forthcoming ...", "his reporting on ...", a quoted book title), the
    sentences after the first, and the author's own name at the start ("Isaac Seitz graduated
    from ..." -> "graduated from ...", "Jane Doe is a senior fellow ..." -> "senior fellow ...")."""
    q = _first_sentence(" ".join(q.split())).strip(" ,;.")
    if name.strip():
        for cand in (name.strip(), name.split()[-1]):
            m = re.match(rf"(?:(?:dr|prof|professor|mr|ms|mrs)\.?\s+)?{re.escape(cand)}\b,?\s*", q, re.I)
            if m:
                q = q[m.end():]
                q = re.sub(r"^(?:is|was|serves as|served as|works as)\s+(?:(?:an?|the)\s+)?", "", q, flags=re.I)
                break
    if not q:
        return q
    parts = [p.strip() for p in re.split(r",|;", q)]
    kept: list[str] = []
    for k, p in enumerate(parts):
        if not p or (k and _BIO.match(p)) or re.search(r"[“”\"]", p):
            if kept:
                break
            continue
        kept.append(re.sub(r"^(?:(?:a|an|currently|now)\s+)+", "", p, flags=re.I))
    out = ", ".join(kept)
    words = out.split()
    if len(words) > max_words:
        cut = ", ".join(kept[:1])
        out = cut if len(cut.split()) <= max_words else " ".join(words[:max_words])
    return out


def align_quals(authors: list[str], model_authors: list[str], model_quals: list[str], hay: str) -> list[str]:
    """One qual per author, in the cite's author order. The model lists people in the order the
    page shows them (which may differ from the metadata), so match by surname. A qual that is
    not in the page text word for word is dropped."""
    def key(name: str) -> str:
        parts = re.sub(r"[^\w\s'-]", "", name).lower().split()
        return parts[-1] if parts else ""

    if len(model_quals) == len(model_authors):
        by_name = {key(a): q for a, q in zip(model_authors, model_quals) if key(a) and _in_text(q, hay)}
        out = [by_name.get(key(a), "") for a in authors]
        if any(out) or not authors:
            return out
    # no usable pairing: only a single author can safely take a single qual
    ok = [q for q in model_quals if _in_text(q, hay)]
    return [ok[0]] if len(authors) <= 1 and ok else [""] * len(authors)
