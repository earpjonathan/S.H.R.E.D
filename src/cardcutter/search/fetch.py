"""Download search results (HTML or PDF) and turn them into Documents, in parallel.

Pages that are too short, look paywalled, or fail to download are skipped with a reason.
Downloads are cached under data/cache/sources/ (git-ignored) so re-runs are fast.
"""
from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from cardcutter.ingest.source import Document, from_html, from_pdf
from cardcutter.search.providers import USER_AGENT, SearchHit

PAYWALL = re.compile(r"subscribe (now )?to (continue|read)|already a subscriber|create a free account to continue|"
                     r"this content is (only )?available to subscribers|sign in to read|to continue reading", re.I)
MAX_BYTES = 30 * 1024 * 1024


@dataclass
class Fetched:
    hit: SearchHit
    doc: Document | None
    problem: str = ""


def _download(url: str, cache: Path, timeout: float) -> tuple[bytes, str, str]:
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    for ext, kind in (("pdf", "application/pdf"), ("html", "text/html")):
        hit = cache / f"{key}.{ext}"
        if hit.exists():
            final = (cache / f"{key}.url").read_text() if (cache / f"{key}.url").exists() else url
            return hit.read_bytes(), kind, final
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"}
    with httpx.stream("GET", url, headers=headers, timeout=timeout, follow_redirects=True) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "").lower()
        body = bytearray()
        for chunk in r.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError("file too large")
        final = str(r.url)
    is_pdf = "pdf" in ctype or bytes(body[:5]) == b"%PDF-"
    if not is_pdf and "html" not in ctype and "xml" not in ctype and ctype:
        raise ValueError(f"unsupported content type {ctype.split(';')[0]}")
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{key}.{'pdf' if is_pdf else 'html'}").write_bytes(bytes(body))
    (cache / f"{key}.url").write_text(final)
    return bytes(body), "application/pdf" if is_pdf else "text/html", final


def fetch_one(hit: SearchHit, cache: Path, *, timeout: float = 20.0, min_words: int = 250) -> Fetched:
    problem = ""
    for url in [hit.url] + hit.alt_urls:
        try:
            body, kind, final = _download(url, cache, timeout)
            if kind == "application/pdf":
                path = cache / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.pdf"
                doc = from_pdf(path)
            else:
                doc = from_html(body.decode("utf-8", errors="replace"), final)
        except Exception as e:
            problem = f"{type(e).__name__}: {str(e)[:120]}"
            continue
        head = " ".join(doc.paragraphs[:3] + doc.paragraphs[-3:])
        if doc.words < min_words:
            problem = f"too short ({doc.words} words)"
            continue
        if PAYWALL.search(head) and doc.words < 900:
            problem = "looks paywalled"
            continue
        if hit.meta is not None:  # provider metadata (OpenAlex, news) is more reliable than page scraping
            doc.meta = hit.meta.merged(doc.meta)
        if not doc.meta.url:
            doc.meta.url = url
        doc.origin = url
        return Fetched(hit, doc)
    return Fetched(hit, None, problem or "no usable text")


def fetch_many(hits: list[SearchHit], cache: Path, *, limit: int, workers: int = 6, timeout: float = 20.0,
               min_words: int = 250, say: Callable[[str], None] = lambda _m: None) -> list[Fetched]:
    """Fetch up to `limit` usable documents, trying hits in order (more are tried if some fail)."""
    out: list[Fetched] = []
    ok = 0
    pending = list(hits)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending and ok < limit:
            batch, pending = pending[: max(limit - ok, 1) + 2], pending[max(limit - ok, 1) + 2:]
            futures = {pool.submit(fetch_one, h, cache, timeout=timeout, min_words=min_words): h for h in batch}
            for f in as_completed(futures):
                res = f.result()
                out.append(res)
                ok += res.doc is not None
            say(f"read {ok} usable article(s) of {len(out)} tried")
    order = {id(h): k for k, h in enumerate(hits)}
    out.sort(key=lambda r: order.get(id(r.hit), 0))
    return out
