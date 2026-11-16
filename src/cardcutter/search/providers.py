"""Search providers for automatic source finding. All free, none needs a key:

- "web":      general web results via the `ddgs` metasearch library
- "news":     dated news articles via `ddgs`
- "openalex": open-access scholarly works via the OpenAlex API (authors, date, journal, DOI)

Only the search query leaves this machine; no personal details are sent.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from cardcutter.cite.meta import CiteMeta

USER_AGENT = "Mozilla/5.0 (compatible; CardCutter/0.1; personal research tool)"


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    provider: str = ""
    query: str = ""
    date: str = ""
    meta: CiteMeta | None = None     # set when the provider knows authors / journal / DOI
    rank: int = 0                    # position within its query's results
    alt_urls: list[str] = field(default_factory=list)


def norm_url(url: str) -> str:
    """For de-duplication: no scheme, no 'www.', no fragment or tracking query, no trailing slash."""
    u = urlsplit(url.strip())
    host = u.netloc.lower().removeprefix("www.")
    query = "&".join(q for q in u.query.split("&") if q and not q.lower().startswith(("utm_", "ref=", "fbclid", "gclid")))
    return urlunsplit(("", host, u.path.rstrip("/"), query, "")).lstrip("/")


def domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def blocked(url: str, blocked_domains: list[str]) -> bool:
    d = domain(url)
    return any(d == b or d.endswith("." + b) for b in blocked_domains)


_BACKOFF = (2.0, 6.0, 15.0)


def _ddgs(kind: str, query: str, region: str, n: int, tries: int = len(_BACKOFF) + 1) -> list[dict]:
    """ddgs fails intermittently when queries come fast (rate limits, a flaky backend); retry with
    backoff. Its rate limits outlast a few seconds: on 2026-11-12 auto mode's second search lost
    every web query to them with waits of 1.5 and 3 s, so the waits are now 2, 6 and 15 s."""
    import time

    from ddgs import DDGS

    for attempt in range(tries):
        try:
            return getattr(DDGS(), kind)(query, region=region, max_results=n)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])
    return []


def web(query: str, n: int, region: str = "us-en", **_: object) -> list[SearchHit]:
    out = []
    for k, r in enumerate(_ddgs("text", query, region, n)):
        if r.get("href"):
            out.append(SearchHit(r["href"], r.get("title", ""), r.get("body", ""), "web", query, rank=k))
    return out


def news(query: str, n: int, region: str = "us-en", **_: object) -> list[SearchHit]:
    out = []
    for k, r in enumerate(_ddgs("news", query, region, n)):
        url = r.get("url") or r.get("href")
        if url:
            meta = CiteMeta(date=(r.get("date") or "")[:10], publication=r.get("source") or "", title=r.get("title") or "",
                            url=url, sources=["news search"])
            out.append(SearchHit(url, r.get("title", ""), r.get("body", ""), "news", query, (r.get("date") or "")[:10], meta, k))
    return out


def openalex(query: str, n: int, max_age_years: int = 0, timeout: float = 20.0, **_: object) -> list[SearchHit]:
    filt = "is_oa:true,has_fulltext:true"
    if max_age_years:
        filt += f",from_publication_date:{date.today().year - max_age_years}-01-01"
    headers = {"User-Agent": USER_AGENT}
    key = os.environ.get("OPENALEX_API_KEY", "").strip()      # optional, free; from .env, never logged
    if key:
        headers["Authorization"] = f"Bearer {key}"
    r = httpx.get("https://api.openalex.org/works", timeout=timeout, headers=headers, params={
        "search": query, "filter": filt, "per-page": n,
        "select": "title,publication_date,doi,authorships,primary_location,best_oa_location,open_access,type",
    })
    r.raise_for_status()
    out = []
    for k, w in enumerate(r.json().get("results", [])):
        best = w.get("best_oa_location") or {}
        urls = [u for u in (best.get("pdf_url"), best.get("landing_page_url"), (w.get("open_access") or {}).get("oa_url"),
                            w.get("doi")) if u]
        if not urls:
            continue
        src = ((w.get("primary_location") or {}).get("source") or {})
        doi = re.sub(r"^https?://doi.org/", "", w.get("doi") or "")
        meta = CiteMeta(
            authors=[a["author"]["display_name"] for a in w.get("authorships", []) if (a.get("author") or {}).get("display_name")][:6],
            date=w.get("publication_date") or "", title=w.get("title") or "", publication=src.get("display_name") or "",
            url=urls[0], doi=doi, sources=["openalex"],
        )
        out.append(SearchHit(urls[0], w.get("title") or "", "", "openalex", query, meta.date, meta, k, alt_urls=urls[1:]))
    return out


PROVIDERS: dict[str, Callable[..., list[SearchHit]]] = {"web": web, "news": news, "openalex": openalex}


def search_all(queries: list[str], providers: list[str], *, per_query: int, region: str, max_age_years: int,
               blocked_domains: list[str], say: Callable[[str], None]) -> list[SearchHit]:
    """Every provider x every query, de-duplicated, blocked domains dropped, interleaved by rank
    so no single query or provider crowds out the rest."""
    lists: list[list[SearchHit]] = []
    for name in providers:
        fn = PROVIDERS.get(name)
        if fn is None:
            say(f"unknown search provider {name!r}; skipped")
            continue
        for q in queries:
            try:
                hits = fn(q, per_query, region=region, max_age_years=max_age_years)
            except Exception as e:  # rate limits, network: keep going with what we have
                if getattr(getattr(e, "response", None), "status_code", None) == 429:
                    hint = (" (its free daily budget without an API key is used up; OpenAlex gives free keys:"
                            " put one in .env as OPENALEX_API_KEY)") if name == "openalex" else ""
                    say(f"{name} search is rate-limited (HTTP 429); skipping it for this search{hint}")
                    break
                say(f"{name} search failed for {q!r}: {type(e).__name__}")
                continue
            lists.append([h for h in hits if not blocked(h.url, blocked_domains)])
    seen: set[str] = set()
    merged: list[SearchHit] = []
    for k in range(max((len(x) for x in lists), default=0)):
        for hits in lists:
            if k < len(hits):
                key = norm_url(hits[k].url)
                if key not in seen:
                    seen.add(key)
                    merged.append(hits[k])
    return merged
