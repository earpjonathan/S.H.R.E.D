"""One-click cards: claim -> search queries -> web/news/scholarly search -> fetch -> rank -> cut.

Every card is still cut from text actually downloaded from the source and verified
character for character against it, exactly like a card from a source you pick yourself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from cardcutter.config import Config
from cardcutter.cutter.article import Draft, cut_document
from cardcutter.llm import prompts, schemas
from cardcutter.llm.base import BackendError
from cardcutter.llm.registry import Registry
from cardcutter.search.fetch import Fetched, fetch_many
from cardcutter.search.providers import domain, norm_url, search_all
from cardcutter.search.rank import Source, rank_sources
from cardcutter.verify.integrity import IntegrityError

Say = Callable[[str], None]

# Pages a judge won't accept as evidence whatever the model thinks of them: reference/glossary
# pages, essay mills and homework sites.
_REFERENCE_PATH = re.compile(r"/(glossary|dictionary|definitions?|encyclopedia|wiki)(/|$)", re.I)
_LOW_CRED_SITES = {"wikipedia.org", "wikiwand.com", "britannica.com", "ukessays.com", "studocu.com",
                   "coursehero.com", "bartleby.com", "ivypanda.com", "gradesfixer.com", "papersowl.com",
                   "edubirdie.com", "studycorgi.com", "quizlet.com", "brainly.com", "enotes.com",
                   "123helpme.com", "ipl.org", "kibin.com", "samplius.com", "writix.co.uk"}


# Sites that republish other outlets' stories: one card per outlet, not one per aggregator.
_AGGREGATORS = {"yahoo.com", "msn.com", "aol.com", "newsbreak.com", "flipboard.com", "news.google.com"}


def _site_key(src: Source) -> str:
    """One card per site. A syndicated story (Forbes or AP "via Yahoo News") counts as its own
    outlet, not as Yahoo."""
    d = domain(src.doc.origin)
    if any(d == a or d.endswith("." + a) for a in _AGGREGATORS):
        outlet = (src.doc.meta.publication or "").split("·")[0].strip().lower()
        return f"{d}|{outlet or src.doc.origin}"
    return d


# Tabloids: a card from one still counts, but ranks below real outlets (2026-11-12: auto mode's
# NATO card came from The US Sun).
_TABLOIDS = {"the-sun.com", "thesun.co.uk", "dailymail.co.uk", "dailystar.co.uk", "express.co.uk",
             "mirror.co.uk", "dailyrecord.co.uk", "nationalenquirer.com"}


def credibility_cap(url: str) -> int:
    """1 for reference pages and essay/homework sites, 2 for tabloids, else 5 (no cap)."""
    d = domain(url)
    if any(d == s or d.endswith("." + s) for s in _LOW_CRED_SITES) or _REFERENCE_PATH.search(url.split("?")[0]):
        return 1
    if any(d == s or d.endswith("." + s) for s in _TABLOIDS):
        return 2
    return 5


@dataclass
class Found:
    queries: list[str]
    sources: list[Source]
    failed: list[Fetched] = field(default_factory=list)
    hits: int = 0
    seen: set[str] = field(default_factory=set)      # every result URL already considered


def write_queries(reg: Registry, claim: str, context: str = "", n: int = 4, tried: list[str] | None = None,
                  focus: str = "") -> list[str]:
    """Search queries for the claim. `tried` lists queries already run; `focus` says what the
    articles found so far lack (see _focus), so the new queries look for that."""
    tried = tried or []
    listed = "\n".join(f"- {q}" for q in tried) + "\n"
    if focus:
        note = (("These queries were already run:\n" + listed) if tried else "") + focus + "\n"
    else:
        note = ("These queries were already run and found too few articles that argue the claim. Try other "
                "angles and wording (other actors, the mechanism in other words, the impact):\n" + listed) if tried else ""
    try:
        res = reg.call("queries", prompts.render("queries", claim=claim, context=context, n=n, tried=note),
                       schemas.QUERIES, "queries")
        qs = [" ".join(q.replace('"', " ").split()) for q in res.data["queries"]]
    except BackendError:
        qs = []
    old = {q.lower() for q in tried}
    qs = [q for q in dict.fromkeys(qs) if 2 <= len(q.split()) <= 16 and q.lower() not in old][:n]
    if len(qs) < 2 and not tried:
        qs.append(" ".join(claim.split()[:16]))
    return qs


def screen_sources(reg: Registry, claim: str, sources: list[Source], top: int = 8, limit: int = 24,
                   say: Say = lambda _m: None) -> list[Source]:
    """Ask the model, `top` sources per call, whether each source's best passage argues FOR the claim.
    Only sources that do (strength 3+) are kept, strongest first: a card is never cut from a source
    the check set aside. Sources past `limit`, or in a batch whose check failed, follow unscreened."""
    kept: list[Source] = []
    unscreened: list[Source] = list(sources[limit:])
    head = sources[:limit]
    if not head:
        return sources
    say(f"checking which of {len(head)} sources argue the claim")
    for i in range(0, len(head), top):
        batch = head[i:i + top]
        blocks = []
        for k, src in enumerate(batch, 1):
            a, b = src.best_paras
            text = " ".join(" ".join(src.doc.paragraphs[a:b + 1]).split()[:220])
            m = src.doc.meta
            who = ", ".join(m.authors[:3]) if m.authors else "no named author"
            blocks.append(f"[{k}] {m.publication or domain(src.doc.origin)} | {(m.title or src.hit.title)[:90]} | "
                          f"by {who} | {domain(src.doc.origin)}\n{text}")
        try:
            res = reg.call("screen", prompts.render("screen", claim=claim, passages="\n\n".join(blocks)),
                           schemas.SCREEN, "screen")
        except BackendError as e:
            say(f"stance check failed ({type(e).__name__}); keeping {len(batch)} source(s) unchecked")
            unscreened.extend(batch)
            continue
        verdicts = {int(r["id"]): r for r in res.data["results"]}
        for k, src in enumerate(batch, 1):
            v = verdicts.get(k)
            if v is None:
                unscreened.append(src)
                continue
            strength = max(1, min(5, int(v["strength"])))
            cred = max(1, min(5, int(v.get("credible", 3)), credibility_cap(src.doc.origin)))
            src.reasons.append(f"argues the claim {strength}/5, source {cred}/5: {v['reason'][:80]}" if v["supports"]
                               else f"does not argue the claim: {v['reason'][:80]}")
            src.stance, src.credible = (strength if v["supports"] else 0), cred
            if v["supports"] and strength >= 3 and cred >= 2:
                src.score += 0.04 * (strength - 3) + 0.03 * (cred - 3)
                kept.append(src)
    dropped = len(head) - len(kept) - sum(1 for x in unscreened if x in head)
    if dropped:
        say(f"set aside {dropped} source(s) that don't argue the claim")
    kept.sort(key=_screen_rank)
    return kept + unscreened


def _screen_rank(x: Source) -> tuple[bool, int, float]:
    """Checked sources first; then how directly the source argues the claim plus how far a judge
    trusts it; then search relevance."""
    return (x.stance is None, -((x.stance or 0) + (getattr(x, "credible", None) or 3)), -x.score)


def find_sources(reg: Registry, cfg: Config, claim: str, *, context: str = "", want: int = 3,
                 say: Say = lambda _m: None, focus: str = "", tried: list[str] | None = None,
                 seen: set[str] | None = None) -> Found:
    """Search, read, rank and stance-check sources. When fewer than `want` sites argue the claim,
    search again with new queries (up to search.rounds rounds).

    For auto mode's second search (more_sources): `focus` says what the articles found so far
    lack, `tried` lists the queries already run, and results in `seen` are skipped. That search
    runs one round."""
    s = cfg.get("search") or {}
    n_q, rounds = int(s.get("queries", 4)), max(1, int(s.get("rounds", 2)))
    if focus:
        rounds = 1
    queries: list[str] = []
    sources: list[Source] = []
    failed: list[Fetched] = []
    seen = set(seen or ())
    n_hits = 0
    for rnd in range(rounds):
        say("writing search queries" if rnd == 0 and not tried else "writing new search queries")
        qs = write_queries(reg, claim, context, n_q, tried=(tried or []) + queries, focus=focus)
        if not qs:
            break
        queries += qs
        say("searching: " + " | ".join(qs))
        hits = search_all(qs, list(s.get("providers", ["web", "news", "openalex"])),
                          per_query=int(s.get("results_per_query", 8)), region=s.get("region", "us-en"),
                          max_age_years=int(s.get("max_age_years", 0)), blocked_domains=list(s.get("blocked_domains", [])),
                          say=say)
        hits = [h for h in hits if norm_url(h.url) not in seen]
        seen |= {norm_url(h.url) for h in hits}
        n_hits += len(hits)
        if not hits:
            continue
        say(f"{len(hits)} new results; reading the most promising")
        fetched = fetch_many(hits, cfg.root / "data" / "cache" / "sources", limit=int(s.get("max_fetch", 14)),
                             timeout=float(s.get("fetch_timeout", 20)), min_words=int(s.get("min_words", 250)), say=say)
        failed += [f for f in fetched if f.doc is None]
        new = rank_sources(reg, claim, fetched, say=say)
        floor = float(s.get("min_relevance", 0.0))
        new = [x for x in new if x.relevance >= floor]
        if reg.has_stage("screen"):
            new = screen_sources(reg, claim, new, top=int(s.get("screen_top", 8)),
                                 limit=int(s.get("screen_max", 24)), say=say)
        sources += new
        sites = {domain(x.doc.origin) for x in sources if x.stance is None or x.stance >= 3}
        if len(sites) >= want or rnd == rounds - 1:
            break
        say(f"only {len(sites)} site(s) argue the claim; searching again")
    if not n_hits:
        raise ValueError("the search found nothing; try wording the claim differently, or give a source yourself")
    sources.sort(key=_screen_rank)
    if not sources:
        raise ValueError("none of the articles found argues the claim (or none could be read); "
                         "try wording the claim differently, or give a source yourself")
    return Found(queries, sources, failed, n_hits, seen)


def auto_cut(reg: Registry, cfg: Config, claim: str, sources: list[Source], *, n_cards: int = 3, context: str = "",
             retriever: Any = None, cite_format: str | None = None, say: Say = lambda _m: None,
             on_draft: Callable[[Source, Draft], None] | None = None,
             search_more: Callable[[str], list[Source]] | None = None) -> tuple[list[tuple[Source, Draft]], list[str]]:
    """Cut one card from each of the best sources (one per site) until n_cards succeed.

    A card whose author argues against its claim is never used. A card whose read-back is a
    clear failure (2 or lower after its retry) is held in reserve while the next sources are
    tried; reserves fill any places still empty at the end. (An author who only qualifies the
    claim gets a warning on the card: academic hedging like "could" is too common to demote.)

    Second search: once search.link_after cards have failed the claim check (or the sources run
    out after at least one did) and the cards still aren't all found, auto mode searches once
    more with `search_more`, aimed at what those cards lack (_focus: the part of the claim no
    card has shown, or the tie between the parts). The new sources are cut first, with
    search.link_tries more attempts; a site whose card was only held back may be tried again
    with another article."""
    sc = cfg.get("search") or {}
    done: list[tuple[Source, Draft]] = []
    reserve: list[tuple[Source, Draft]] = []
    skipped: list[str] = []
    used_domains: set[str] = set()
    done_sites: set[str] = set()
    tries = int(sc.get("max_cut_attempts", n_cards + 3))
    link_after, link_tries = int(sc.get("link_after", 3)), int(sc.get("link_tries", 4))
    searched = search_more is None or not bool(sc.get("link_search", True))
    misses = 0                          # cards the claim check dropped or held back
    checks: list[ClaimCheck] = []
    queue = list(sources)
    known = {norm_url(x.doc.origin) for x in sources}
    second: set[int] = set()            # ids of the sources the second search found
    second_sites: set[str] = set()
    checking = bool(cfg.get("search.claim_check", True)) and reg.has_stage("readback")
    parts = claim_parts(reg, claim) if checking and bool(cfg.get("search.claim_parts", True)) else [claim]
    if len(parts) > 1:
        say(f"a card must show {len(parts)} parts of the claim: "
            + "; ".join(f"({i}) {p}" for i, p in enumerate(parts, 1)))
    while len(done) < n_cards:
        if not searched and misses and (misses >= link_after or not queue or tries <= 0):
            searched = True
            focus = _focus(parts, checks)
            say(f"no card proves the whole claim yet; searching again. {focus}")
            new = [x for x in search_more(focus) if norm_url(x.doc.origin) not in known and _site_key(x) not in done_sites]
            known |= {norm_url(x.doc.origin) for x in new}
            second |= {id(x) for x in new}
            queue = new + queue
            tries = max(tries, link_tries)
            say(f"the second search found {len(new)} new source(s); cutting those first" if new else
                "the second search found no new sources")
        if not queue or tries <= 0:
            break
        src = queue.pop(0)
        d = domain(src.doc.origin)
        site = _site_key(src)
        if id(src) in second:
            if site in done_sites or site in second_sites:
                continue
            second_sites.add(site)
        elif site in used_domains:
            continue
        tries -= 1
        label = (src.doc.meta.title or d)[:60]
        tag = lambda m, k=len(done) + 1: say(f"card {k}/{n_cards} ({label}): {m}")  # noqa: E731
        try:
            [draft] = cut_document(reg, cfg, src.doc, claim, context=context, candidates=1, retriever=retriever,
                                   cite_format=cite_format, progress=tag)
        except (IntegrityError, ValueError, BackendError) as e:
            skipped.append(f"{d}: {type(e).__name__}: {str(e)[:160]}")
            say(f"skipped {d}: {type(e).__name__}")
            continue
        used_domains.add(site)
        view = getattr(draft, "author_view", None) or {}
        if view.get("verdict") == "disputes":
            quote = f" (“{view['author_conclusion'][:120]}”)" if view.get("author_conclusion") else ""
            skipped.append(f"{d}: the author argues against the claim{quote}")
            say(f"the author of {d} argues against the claim; not using that card")
            continue
        check = _proves_claim(reg, claim, draft, parts) if checking else None
        on_claim = check.score if check else None
        if check is not None:
            checks.append(check)
            try:
                draft.claim_check = check
            except AttributeError:
                pass
            if check.score <= 3:
                misses += 1
        if on_claim is not None and on_claim <= 2:
            skipped.append(f"{d}: the card doesn't prove the claim (claim check {on_claim}/5"
                           f"{': ' + check.reason if check.reason else ''})")
            say(f"the card from {d} doesn't prove the claim; trying another source")
            continue
        if _weak(draft) or (on_claim is not None and on_claim <= 3):
            reserve.append((src, draft))
            if on_claim is not None and on_claim <= 3 and not _weak(draft):
                if check.missing:
                    say(f"the card from {d} proves only part of the claim (it never shows: "
                        f"“{'”; “'.join(check.missing)}”); trying another source")
                else:
                    say(f"the card from {d} barely proves the claim (claim check {on_claim}/5); trying another source")
                continue
            say(f"the card from {d} reads poorly (read-back {draft.result.readback['min']}/5); trying another source")
            continue
        _note_weak_source(src, draft)
        done.append((src, draft))
        done_sites.add(site)
        if on_draft:
            on_draft(src, draft)
    # Reserves fill the places still empty. A card that shows a part of the claim no card shows
    # yet goes first (two half cards together prove the whole claim), then the best read.
    usable = []
    for src, draft in reserve:
        if _usable(draft):
            usable.append((src, draft))
        else:
            rb = draft.result.readback or {}
            skipped.append(f"{domain(src.doc.origin)}: read-back too weak to use (grammatical {rb.get('grammatical')}, "
                           f"coherent {rb.get('coherent')}, proves the tag {rb.get('proves_tag')})")
    shown = set().union(*(_covered(dr, len(parts)) for _, dr in done)) if done else set()
    while len(done) < n_cards:
        fits = [sd for sd in usable if _site_key(sd[0]) not in done_sites]     # one card per site
        if not fits:
            break
        pick = max(fits, key=lambda sd: (len(_covered(sd[1], len(parts)) - shown),
                                          len(_covered(sd[1], len(parts))), _card_rank(sd)))
        usable.remove(pick)
        _note_weak_source(*pick)
        shown |= _covered(pick[1], len(parts))
        done_sites.add(_site_key(pick[0]))
        done.append(pick)
        if on_draft:
            on_draft(*pick)
    for src, draft in usable:
        skipped.append(f"{domain(src.doc.origin)}: {_reserve_reason(draft)}; replaced by a stronger card")
    done.sort(key=lambda sd: (len(_covered(sd[1], len(parts))),) + _card_rank(sd), reverse=True)
    return done, skipped


@dataclass
class ClaimCheck:
    """The claim check's verdict on one card: a 1-5 score, and for each part of the claim whether
    the read-aloud text shows it ("yes", "partly" or "no")."""
    score: int
    reason: str
    parts: list[str]
    shown: list[str]

    @property
    def missing(self) -> list[str]:
        return [p for p, s in zip(self.parts, self.shown) if s == "no"]

    def warning(self) -> str:
        """Shown on a held-back card that filled an empty place."""
        if self.missing:
            return ("proves only part of the claim: the read-aloud text never shows "
                    + "; ".join(f"“{p}”" for p in self.missing))
        if self.score <= 3:
            return f"only partly proves the claim (claim check {self.score}/5{': ' + self.reason if self.reason else ''})"
        return ""


def _note_weak_source(src: Source, d: Draft) -> None:
    """A note on a card whose source the screen rated 2/5 or lower for credibility."""
    cred = getattr(src, "credible", None)
    notes = getattr(d, "notes", None)
    if cred is not None and cred <= 2 and isinstance(notes, list):
        notes.append(f"weak source: the screen rated {domain(src.doc.origin)} {cred}/5 for credibility; "
                     "look for a stronger source for this warrant")


def _focus(parts: list[str], checks: list[ClaimCheck]) -> str:
    """What the cards cut so far lack, for the second search's queries."""
    if len(parts) <= 1:
        return ("The articles found so far only describe events or argue a related point. Write queries "
                "for articles that argue the claim itself, outright.")
    shown = {i for c in checks for i, s in enumerate(c.shown) if s == "yes"}
    never = [p for i, p in enumerate(parts) if i not in shown]
    if never:
        return ("The articles found so far never show " + "; ".join(f"“{p}”" for p in never) + ". Write queries "
                "for articles that make that point and tie it to the rest of the claim.")
    return ("The articles found so far show each part of the claim on its own but never tie them together ("
            + " / ".join(f"“{p}”" for p in parts) + "). Write queries for articles that make the whole link "
            "in one place, with words from both parts in each query.")


def more_sources(reg: Registry, cfg: Config, claim: str, found: Found, *, context: str = "", want: int = 3,
                 say: Say = lambda _m: None,
                 on_new: Callable[[Found], None] | None = None) -> Callable[[str], list[Source]]:
    """auto_cut's second search: new queries aimed at `focus`, skipping every result `found`
    already considered. Adds the new queries and results to `found`."""
    def search(focus: str) -> list[Source]:
        try:
            more = find_sources(reg, cfg, claim, context=context, want=want, say=say, focus=focus,
                                tried=found.queries, seen=found.seen)
        except ValueError as e:
            say(f"the second search found nothing usable ({e})")
            return []
        found.queries += more.queries
        found.sources += more.sources
        found.failed += more.failed
        found.hits += more.hits
        found.seen |= more.seen
        if on_new:
            on_new(more)
        return more.sources
    return search


def claim_parts(reg: Registry, claim: str, max_parts: int = 3) -> list[str]:
    """The separate things a card must show to prove the whole claim (prompt claimparts.md): one
    for each side of a claim that links two things (a cause and its effect, a condition and what
    follows), one for a simple claim. The claim itself if the model fails."""
    try:
        res = reg.call("readback", prompts.render("claimparts", claim=claim), schemas.CLAIMPARTS, "claimparts")
        parts = [" ".join(str(p).split()) for p in res.data["parts"]]
    except (BackendError, KeyError, TypeError, AttributeError):
        return [claim]
    parts = [p for p in dict.fromkeys(parts) if len(p.split()) >= 2][:max_parts]
    return parts or [claim]


def _covered(d: Draft, n_parts: int) -> set[int]:
    """Which parts of the claim a card shows (all of them when it wasn't checked)."""
    check = getattr(d, "claim_check", None)
    if not isinstance(check, ClaimCheck):
        return set(range(n_parts))
    return {i for i, s in enumerate(check.shown) if s != "no"}


def _reserve_reason(d: Draft) -> str:
    check = getattr(d, "claim_check", None)
    if isinstance(check, ClaimCheck) and check.missing:
        return "proves only part of the claim"
    if isinstance(check, ClaimCheck) and check.score <= 3:
        return f"claim check {check.score}/5"
    return f"read-back {(d.result.readback or {}).get('min')}/5"


def _proves_claim(reg: Registry, claim: str, d: Draft, parts: list[str] | None = None) -> ClaimCheck | None:
    """Is the card, as read aloud, evidence for the claim the user searched for? (A card can prove
    the tag the model wrote and still drift off the claim.) Its own prompt, not the read-back
    judge's: that judge wants the whole tag proved word for word.

    A claim with one part gets the whole-claim prompt (claimcheck.md), where a card that proves
    one central piece of the claim counts. For a claim with two or more parts (claim_parts), the
    judge says which parts the read-aloud text shows (claimcheck_parts.md). A card that never shows
    one of them (stockpiles drained, but nothing about Taiwan) scores 3 at most, so auto mode holds
    it in reserve and keeps looking for a card that proves the whole claim; one that shows a part
    is never dropped. (Calibrated on 24 cards, 2026-11-12: the parts prompt held back all four
    half-proved cards, which the whole-claim prompt had passed at 4/5, but it was stricter than
    the whole-claim prompt on one-part claims.)"""
    parts = parts or [claim]
    card = getattr(d, "card", None)
    said = card.body.read_aloud() if card is not None else ""
    if not said.strip() or not reg.has_stage("readback"):
        return None
    try:
        if len(parts) == 1:
            res = reg.call("readback", prompts.render("claimcheck", claim=claim, tag=card.tag.text, read_aloud=said),
                           schemas.CLAIMCHECK, "claimcheck")
        else:
            listed = "\n".join(f"{i}. {p}" for i, p in enumerate(parts, 1))
            res = reg.call("readback", prompts.render("claimcheck_parts", claim=claim, parts=listed, tag=card.tag.text,
                                                      read_aloud=said), schemas.CLAIMCHECK_PARTS, "claimcheck")
        score = max(1, min(5, int(res.data["supports"])))
        reason = str(res.data.get("reason", ""))[:120]
        verdicts = {int(v["n"]): v for v in res.data.get("parts") or [] if isinstance(v, dict) and "n" in v}
    except (BackendError, KeyError, TypeError, ValueError, AttributeError):
        return None
    if len(parts) == 1:
        return ClaimCheck(score, reason, list(parts), ["yes" if score >= 4 else "partly" if score == 3 else "no"])
    shown = []
    for i in range(1, len(parts) + 1):
        v = verdicts.get(i) or {}
        s = str(v.get("shown", "partly")).lower()
        shown.append(s if s in ("yes", "partly", "no") else "partly")
    if "no" in shown:
        score = min(score, 3)
    if "yes" in shown:          # a card that shows a part is never dropped, only held back
        score = max(score, 3)
    return ClaimCheck(score, reason, list(parts), shown)


def _weak(d: Draft) -> bool:
    rb = d.result.readback
    return bool(rb) and float(rb.get("min", 3)) <= 2


def _usable(d: Draft) -> bool:
    """A held-back card fills an empty place only if it still proves its tag and parses at all."""
    rb = d.result.readback
    return not rb or (int(rb.get("proves_tag", 3)) >= 3 and int(rb.get("grammatical", 3)) + int(rb.get("coherent", 3)) >= 5)


def _card_rank(sd: tuple[Source, Draft]) -> tuple[float, float]:
    src, d = sd
    rb = d.result.readback or {}
    return (float(rb.get("proves_tag", 3)) + float(rb.get("min", 3)) / 2, src.score)
