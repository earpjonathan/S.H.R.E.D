"""Automatic source finding, with fake search providers and downloads (no network, no model)."""
from __future__ import annotations

import time

import pytest

from cardcutter.cite.meta import CiteMeta
from cardcutter.ingest.source import from_text
from cardcutter.search import auto as auto_mod
from cardcutter.search import fetch as fetch_mod
from cardcutter.search import providers as prov
from cardcutter.search.fetch import Fetched, fetch_one
from cardcutter.search.providers import SearchHit, blocked, norm_url, search_all
from cardcutter.search.rank import chunks, rank_sources
from tests.fakes import FakeBackend, make_config, registry_with

ON_TOPIC = "\n\n".join(f"Finding {k}: experts warn that disputes over marine protected areas will escalate. " * 3
                       for k in range(12))
OFF_TOPIC = ("Corn yields rose in Iowa this year as rainfall returned to normal levels. " * 3 + "\n\n") * 6


def test_norm_url_and_blocking():
    assert norm_url("https://www.Example.com/a/?utm_source=x&id=3#top") == "example.com/a?id=3"
    assert norm_url("http://example.com/a") == norm_url("https://example.com/a/")
    assert blocked("https://en.wikipedia.org/wiki/X", ["wikipedia.org"])
    assert not blocked("https://notwikipedia.org/x", ["wikipedia.org"])


def test_syndicated_stories_count_as_their_own_outlet():
    a = _source("https://www.yahoo.com/news/a.html", 0.9)
    b = _source("https://www.yahoo.com/news/b.html", 0.8)
    c = _source("https://www.rand.org/x.html", 0.7)
    a.doc.meta.publication, b.doc.meta.publication = "Forbes · via Yahoo News", "Associated Press · via Yahoo News"
    keys = {auto_mod._site_key(x) for x in (a, b, c)}
    assert len(keys) == 3 and "rand.org" in keys
    b.doc.meta.publication = "Forbes · via Yahoo News"
    assert auto_mod._site_key(a) == auto_mod._site_key(b)


def test_reference_pages_and_essay_sites_are_not_credible():
    cap = auto_mod.credibility_cap
    assert cap("https://modeldiplomat.com/learn/glossary/porcupine-strategy") == 1
    assert cap("https://www.ukessays.com/essays/politics/x.php") == 1
    assert cap("https://en.wikipedia.org/wiki/Deterrence") == 1
    assert cap("https://www.rand.org/pubs/research_reports/RR1.html") == 5
    assert cap("https://www.bbc.co.uk/news/articles/ceqq2zn3zw6o") == 5
    assert cap("https://www.the-sun.com/news/17290789/putin-ukraine-weapons-ww3-army/") == 2   # tabloid


def test_cards_from_weak_sources_carry_a_note(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    monkeypatch.setattr(auto_mod, "cut_document", lambda reg, cfg, doc, claim, **kw: [_judged_card("Text here.")])
    weak, good = _source("https://www.the-sun.com/news/1", 0.9), _source("https://www.bbc.co.uk/news/2", 0.8)
    weak.credible, good.credible = 2, 4
    done, _ = auto_mod.auto_cut(registry_with(cfg), cfg, "claim", [weak, good], n_cards=2)
    notes = {s_.doc.origin: d.notes for s_, d in done}
    assert any("weak source" in n for n in notes["https://www.the-sun.com/news/1"])
    assert notes["https://www.bbc.co.uk/news/2"] == []


def test_search_all_interleaves_dedupes_and_survives_failures(monkeypatch):
    def fake_web(q, n, **_):
        return [SearchHit(f"https://a.com/{q}/{k}", provider="web", query=q, rank=k) for k in range(3)] + \
               [SearchHit("https://www.shared.org/x/", provider="web", query=q),
                SearchHit("https://en.wikipedia.org/wiki/Y", provider="web", query=q)]

    def broken(q, n, **_):
        raise RuntimeError("rate limited")

    monkeypatch.setitem(prov.PROVIDERS, "web", fake_web)
    monkeypatch.setitem(prov.PROVIDERS, "news", broken)
    notes = []
    hits = search_all(["q1", "q2"], ["web", "news"], per_query=5, region="us-en", max_age_years=0,
                      blocked_domains=["wikipedia.org"], say=notes.append)
    urls = [h.url for h in hits]
    assert urls[:2] == ["https://a.com/q1/0", "https://a.com/q2/0"]          # round robin across queries
    assert sum("shared.org" in u for u in urls) == 1                          # de-duplicated
    assert not any("wikipedia" in u for u in urls)
    assert any("news search failed" in n for n in notes)


def test_a_rate_limited_provider_is_skipped_for_the_rest_of_the_search(monkeypatch):
    import httpx

    calls, said = [], []

    def limited(q, n, **kw):
        calls.append(q)
        req = httpx.Request("GET", "https://api.openalex.org/works")
        raise httpx.HTTPStatusError("429", request=req, response=httpx.Response(429, request=req))

    monkeypatch.setitem(prov.PROVIDERS, "openalex", limited)
    monkeypatch.setitem(prov.PROVIDERS, "web", lambda q, n, **kw: [SearchHit(f"https://a.org/{q}")])
    hits = search_all(["q1", "q2", "q3"], ["openalex", "web"], per_query=3, region="us-en", max_age_years=0,
                      blocked_domains=[], say=said.append)
    assert calls == ["q1"] and len(hits) == 3
    assert any("rate-limited" in m and "OPENALEX_API_KEY" in m for m in said)


def test_openalex_sends_an_api_key_only_when_one_is_set(monkeypatch):
    sent = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    monkeypatch.setattr(prov.httpx, "get", lambda url, **kw: sent.append(kw["headers"]) or R())
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    prov.openalex("q", 3)
    monkeypatch.setenv("OPENALEX_API_KEY", "k123")
    prov.openalex("q", 3)
    assert "Authorization" not in sent[0] and sent[1]["Authorization"] == "Bearer k123"


def test_fetch_one_skips_short_and_paywalled_pages_and_uses_provider_meta(tmp_path, monkeypatch):
    pages = {
        "https://short.com/a": "<html><body><article><p>Too short.</p></article></body></html>",
        "https://wall.com/a": "<html><body><article><p>Subscribe to continue reading this article.</p>"
                              + "<p>" + "word " * 300 + "</p></article></body></html>",
        "https://good.com/a": "<html><head><title>Good</title></head><body><article>"
                              + "".join(f"<p>Point {k}: {'marine protected areas need patrols to work, ' * 8}said expert {k}.</p>"
                                        for k in range(8))
                              + "</article></body></html>",
    }
    monkeypatch.setattr(fetch_mod, "_download", lambda url, cache, timeout: (pages[url].encode(), "text/html", url))
    assert "too short" in fetch_one(SearchHit("https://short.com/a"), tmp_path).problem
    assert fetch_one(SearchHit("https://wall.com/a"), tmp_path).problem == "looks paywalled"
    meta = CiteMeta(authors=["Ana Ruiz"], date="2025-04-02", publication="Marine Policy", sources=["openalex"])
    good = fetch_one(SearchHit("https://good.com/a", meta=meta, provider="openalex"), tmp_path)
    assert good.doc is not None and good.doc.meta.authors == ["Ana Ruiz"] and good.doc.meta.publication == "Marine Policy"
    # a failing primary URL falls through to the alternate
    alt = fetch_one(SearchHit("https://short.com/a", alt_urls=["https://good.com/a"]), tmp_path)
    assert alt.doc is not None and alt.doc.origin == "https://good.com/a"


def test_rank_prefers_the_on_topic_article(tmp_path):
    cfg = make_config(tmp_path)
    on, off = from_text(ON_TOPIC, origin="https://on.org/a"), from_text(OFF_TOPIC, origin="https://off.org/b")
    on.meta.authors, on.meta.date = ["A B"], "2025-01-01"

    def embed(texts):
        return [[1.0, 0.0] if ("marine" in t.lower() or "escalate" in t.lower()) else [0.0, 1.0] for t in texts]

    fake = FakeBackend("fake", lambda s, p: {})
    fake.embed = embed
    reg = registry_with(cfg, fake=fake)
    reg.cfg.data["llm"]["presets"]["local"]["embed"] = "fake"
    ranked = rank_sources(reg, "marine protected area disputes escalate",
                          [Fetched(SearchHit("https://off.org/b"), off), Fetched(SearchHit("https://on.org/a"), on),
                           Fetched(SearchHit("https://dead.org"), None)])
    assert [r.doc.origin for r in ranked] == ["https://on.org/a", "https://off.org/b"]
    assert ranked[0].relevance > 0.9 and "named author" in ranked[0].reasons
    assert len(chunks(on)) >= 2


def _source(origin, score, text=ON_TOPIC):
    from cardcutter.search.rank import Source

    doc = from_text(text, origin=origin)
    return Source(origin[-6:], doc, SearchHit(origin), score, score, (0, 0))


def test_auto_cut_one_card_per_site_and_skips_failures(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    calls = []

    def fake_cut(reg, cfg, doc, claim, **kw):
        calls.append(doc.origin)
        if "bad" in doc.origin:
            from cardcutter.verify.integrity import IntegrityError
            raise IntegrityError(["nothing is highlighted"])

        class R:
            readback = {"proves_tag": 5 if "best" in doc.origin else 3, "min": 4}
        return [type("D", (), {"result": R()})()]

    monkeypatch.setattr(auto_mod, "cut_document", fake_cut)
    sources = [_source("https://a.com/1", 0.9), _source("https://a.com/2", 0.85), _source("https://bad.com/x", 0.8),
               _source("https://best.org/y", 0.7), _source("https://c.net/z", 0.6)]
    done, skipped = auto_mod.auto_cut(registry_with(cfg), cfg, "claim", sources, n_cards=2)
    assert calls == ["https://a.com/1", "https://bad.com/x", "https://best.org/y"]    # a.com/2 skipped: same site
    assert [s.doc.origin for s, _ in done] == ["https://best.org/y", "https://a.com/1"]  # best read-back first
    assert len(skipped) == 1 and "bad.com" in skipped[0]


def test_auto_cut_holds_weak_cards_in_reserve(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    calls = []

    def fake_cut(reg, cfg, doc, claim, **kw):
        calls.append(doc.origin)

        class R:
            readback = ({"proves_tag": 1, "min": 1, "grammatical": 1, "coherent": 2} if "weak" in doc.origin else
                        {"proves_tag": 3, "min": 2, "grammatical": 2, "coherent": 3} if "meh" in doc.origin else
                        {"proves_tag": 4, "min": 4, "grammatical": 4, "coherent": 4})
        return [type("D", (), {"result": R()})()]

    monkeypatch.setattr(auto_mod, "cut_document", fake_cut)
    sources = [_source("https://weak.com/1", 0.9), _source("https://a.com/1", 0.8), _source("https://b.com/1", 0.7),
               _source("https://c.com/1", 0.6)]
    shown = []
    done, skipped = auto_mod.auto_cut(registry_with(cfg), cfg, "claim", sources, n_cards=2,
                                      on_draft=lambda s, d: shown.append(s.doc.origin))
    assert calls == ["https://weak.com/1", "https://a.com/1", "https://b.com/1"]
    assert [s.doc.origin for s, _ in done] == ["https://a.com/1", "https://b.com/1"]
    assert shown == ["https://a.com/1", "https://b.com/1"]       # the weak card never reached the UI
    assert len(skipped) == 1 and "weak.com" in skipped[0]

    # nothing better found: a weak card that still proves its tag fills the empty place;
    # one that doesn't prove its tag is never used
    calls.clear()
    meh = _source("https://meh.com/1", 0.85)
    done, skipped = auto_mod.auto_cut(registry_with(cfg), cfg, "claim", [sources[0], meh, sources[1]], n_cards=3)
    assert [s.doc.origin for s, _ in done] == ["https://a.com/1", "https://meh.com/1"]
    assert any("weak.com" in x and "too weak" in x for x in skipped)


def test_auto_cut_drops_cards_that_drift_off_the_claim(tmp_path, monkeypatch):
    from cardcutter.card import Card, FText

    cfg = make_config(tmp_path)
    judged = []

    def respond(stage, prompt):
        if stage == "claimparts":
            return {"parts": ["the claim"]}
        assert stage == "claimcheck" and "searched for: the claim" in prompt.user
        judged.append(prompt.user)
        assert "parts a card must show" not in prompt.user      # one part: the whole-claim prompt
        return {"supports": 1 if "Putin stays" in prompt.user else 4, "reason": "x"}

    def fake_cut(reg, cfg, doc, claim, **kw):
        body = FText("Putin stays in power." if "off" in doc.origin else "Strikes changed nothing.")
        body.set_span("underline", 0, len(body.text))
        body.set_span("highlight", 0, len(body.text), "yellow")

        class R:
            readback = {"proves_tag": 4, "min": 4, "grammatical": 4, "coherent": 4}
        return [type("D", (), {"result": R(), "card": Card(FText("tag"), FText("cite"), body)})()]

    monkeypatch.setattr(auto_mod, "cut_document", fake_cut)
    sources = [_source("https://off.com/1", 0.9), _source("https://on.com/1", 0.8)]
    done, skipped = auto_mod.auto_cut(registry_with(cfg, fake=FakeBackend("fake", respond)), cfg, "the claim",
                                      sources, n_cards=2)
    assert [s.doc.origin for s, _ in done] == ["https://on.com/1"] and len(judged) == 2
    assert any("off.com" in x and "doesn't prove the claim" in x for x in skipped)


def _judged_card(text):
    from cardcutter.card import Card, FText

    body = FText(text)
    body.set_span("underline", 0, len(body.text))
    body.set_span("highlight", 0, len(body.text), "yellow")

    class R:
        readback = {"proves_tag": 4, "min": 4, "grammatical": 4, "coherent": 4}
        warnings: list = []
    from cardcutter.cutter.article import Draft
    return Draft(Card(FText("tag"), FText("cite"), body), None, R())


def _parts_judge(seen):
    """Claim "aid drains stockpiles needed for Taiwan": two parts. A card shows the stockpile part
    if it says "stockpiles", the Taiwan part if it says "Taiwan"."""
    def respond(stage, prompt):
        if stage == "claimparts":
            return {"parts": ["Aid to Ukraine drains US stockpiles", "Those stockpiles are needed for Taiwan"]}
        assert stage == "claimcheck" and "1. Aid to Ukraine drains US stockpiles" in prompt.user
        said = prompt.user.split("Read aloud:", 1)[1].split("\n", 1)[0]
        seen.append(said.strip())
        a, b = "stockpiles" in said, "Taiwan" in said
        return {"parts": [{"n": 1, "words": "stockpiles" if a else "", "shown": "yes" if a else "no"},
                          {"n": 2, "words": "Taiwan" if b else "", "shown": "yes" if b else "no"}],
                "supports": 4 if a or b else 2, "reason": "x"}
    return respond


def test_auto_cut_holds_cards_that_prove_half_the_claim(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    texts = {"half.com": "Aid drained US stockpiles.", "whole.com": "Aid drained the stockpiles needed for Taiwan.",
             "other.com": "Taiwan needs munitions now."}
    monkeypatch.setattr(auto_mod, "cut_document",
                        lambda reg, cfg, doc, claim, **kw: [_judged_card(texts[doc.origin.split("/")[2]])])
    seen, said = [], []
    sources = [_source("https://half.com/1", 0.9), _source("https://whole.com/1", 0.8), _source("https://other.com/1", 0.7)]
    done, skipped = auto_mod.auto_cut(registry_with(cfg, fake=FakeBackend("fake", _parts_judge(seen))), cfg,
                                      "Aid to Ukraine drains US stockpiles needed for Taiwan", sources, n_cards=1,
                                      say=said.append)
    # the half card (a score of 4 from the judge) is held back; the card that shows both parts wins
    assert [s.doc.origin for s, _ in done] == ["https://whole.com/1"] and len(seen) == 2
    assert any("2 parts" in m for m in said)
    assert any("half.com" in m and "only part of the claim" in m and "needed for Taiwan" in m for m in said)
    assert any("half.com" in x and "only part of the claim" in x and "replaced" in x for x in skipped)
    assert not done[0][1].claim_check.missing and not any("part of the claim" in w for w in done[0][1].warnings)


def test_reserve_cards_cover_the_parts_no_other_card_shows(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    texts = {"a.com": "Aid drained US stockpiles badly.", "b.com": "Aid drained US stockpiles again.",
             "c.com": "Taiwan needs munitions now."}
    monkeypatch.setattr(auto_mod, "cut_document",
                        lambda reg, cfg, doc, claim, **kw: [_judged_card(texts[doc.origin.split("/")[2]])])
    sources = [_source("https://a.com/1", 0.9), _source("https://b.com/1", 0.8), _source("https://c.com/1", 0.7)]
    done, skipped = auto_mod.auto_cut(registry_with(cfg, fake=FakeBackend("fake", _parts_judge([]))), cfg,
                                      "Aid to Ukraine drains US stockpiles needed for Taiwan", sources, n_cards=2)
    # no card proves the whole claim: the two places go to cards that together show both parts
    assert sorted(s.doc.origin for s, _ in done) == ["https://a.com/1", "https://c.com/1"]
    assert all(any("never shows" in w for w in d.warnings) for _, d in done)
    assert any("b.com" in x and "replaced" in x for x in skipped)


def test_claim_check_score_follows_the_parts(tmp_path):
    cfg = make_config(tmp_path)
    card = _judged_card("Aid drained US stockpiles.")

    def judge(supports, shown):
        return registry_with(cfg, fake=FakeBackend("fake", lambda s, p: {
            "parts": [{"n": i, "words": "", "shown": v} for i, v in enumerate(shown, 1)],
            "supports": supports, "reason": "r"}))

    two = ["Aid drains stockpiles", "Stockpiles are needed for Taiwan"]
    c = auto_mod._proves_claim(judge(5, ["yes", "no"]), "claim", card, two)
    assert c.score == 3 and c.missing == ["Stockpiles are needed for Taiwan"] and "never shows" in c.warning()
    assert auto_mod._proves_claim(judge(2, ["yes", "partly"]), "claim", card, two).score == 3   # shows a part: held, not dropped
    assert auto_mod._proves_claim(judge(2, ["no", "partly"]), "claim", card, two).score == 2
    one = registry_with(cfg, fake=FakeBackend("fake", lambda s, p: {"supports": 3, "reason": "r"}))
    c = auto_mod._proves_claim(one, "claim", card, ["one part"])         # one part: the whole-claim score
    assert c.score == 3 and c.shown == ["partly"] and not c.missing and "claim check 3/5: r" in c.warning()
    c = auto_mod._proves_claim(judge(4, ["yes"]), "claim", card, two)   # the judge skipped part 2: unknown, not missing
    assert c.score == 4 and c.shown == ["yes", "partly"] and not c.missing


def test_auto_cut_searches_again_for_the_part_no_card_shows(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    texts = {"https://a.com/1": "Aid drained US stockpiles.", "https://b.com/1": "Aid drained US stockpiles again.",
             "https://c.com/1": "Aid drained US stockpiles badly.", "https://d.com/1": "Aid drained US stockpiles too.",
             "https://a.com/2": "Aid drained the stockpiles needed for Taiwan."}
    cut, shown, foci = [], [], []
    monkeypatch.setattr(auto_mod, "cut_document",
                        lambda reg, cfg, doc, claim, **kw: cut.append(doc.origin) or [_judged_card(texts[doc.origin])])
    round1 = [_source(u, 0.9 - k / 10) for k, u in enumerate(["https://a.com/1", "https://b.com/1", "https://c.com/1",
                                                                "https://d.com/1"])]

    def search_more(focus):
        foci.append(focus)
        return [_source("https://a.com/1", 0.5), _source("https://a.com/2", 0.5)]    # one already seen, one new

    done, skipped = auto_mod.auto_cut(registry_with(cfg, fake=FakeBackend("fake", _parts_judge([]))), cfg,
                                      "Aid to Ukraine drains US stockpiles needed for Taiwan", round1, n_cards=1,
                                      on_draft=lambda s_, d: shown.append(s_.doc.origin), search_more=search_more)
    # three half cards, then one search aimed at the missing part; its new article goes first,
    # and a site whose card was only held back may be tried again
    assert len(foci) == 1 and "never show “Those stockpiles are needed for Taiwan”" in foci[0]
    assert cut == ["https://a.com/1", "https://b.com/1", "https://c.com/1", "https://a.com/2"]
    assert [s_.doc.origin for s_, _ in done] == ["https://a.com/2"] and shown == ["https://a.com/2"]
    assert not any("a.com" in x for x in skipped if "replaced" not in x)


def test_auto_cut_searches_again_when_the_sources_run_out(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    texts = {"https://a.com/1": "Aid drained US stockpiles.", "https://b.com/1": "Taiwan needs munitions now.",
             "https://c.com/1": "Aid drained the stockpiles needed for Taiwan."}
    monkeypatch.setattr(auto_mod, "cut_document",
                        lambda reg, cfg, doc, claim, **kw: [_judged_card(texts[doc.origin])])
    foci = []
    search = lambda f: foci.append(f) or [_source("https://c.com/1", 0.5)]  # noqa: E731
    reg = registry_with(cfg, fake=FakeBackend("fake", _parts_judge([])))
    claim = "Aid to Ukraine drains US stockpiles needed for Taiwan"
    done, _ = auto_mod.auto_cut(reg, cfg, claim, [_source("https://a.com/1", 0.9), _source("https://b.com/1", 0.8)],
                                n_cards=1, search_more=search)
    # each part shown by some card, never both: the search asks for the tie between them
    assert len(foci) == 1 and "never tie them together" in foci[0]
    assert [s_.doc.origin for s_, _ in done] == ["https://c.com/1"]

    # cards that prove the whole claim: no second search
    foci.clear()
    done, _ = auto_mod.auto_cut(reg, cfg, claim, [_source("https://c.com/1", 0.9)], n_cards=1, search_more=search)
    assert not foci and len(done) == 1

    # turned off in the config: none either
    cfg.data.setdefault("search", {})["link_search"] = False
    done, _ = auto_mod.auto_cut(reg, cfg, claim, [_source("https://a.com/1", 0.9)], n_cards=1, search_more=search)
    assert not foci and [s_.doc.origin for s_, _ in done] == ["https://a.com/1"]


def test_write_queries_aims_at_the_focus(tmp_path):
    cfg = make_config(tmp_path)
    prompts_seen = []

    def respond(stage, prompt):
        prompts_seen.append(prompt.user)
        return {"plan": "p", "queries": ["ukraine aid taiwan munitions tradeoff", "old query here"]}

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    qs = auto_mod.write_queries(reg, "claim", tried=["old query here"], focus="The articles never show X.")
    assert qs == ["ukraine aid taiwan munitions tradeoff"]
    assert "The articles never show X." in prompts_seen[0] and "- old query here" in prompts_seen[0]
    assert "too few articles" not in prompts_seen[0]


def test_claim_parts_falls_back_to_the_whole_claim(tmp_path):
    cfg = make_config(tmp_path)
    reg = registry_with(cfg, fake=FakeBackend("fake", lambda s, p: {"parts": ["", "x", "Aid drains stockpiles",
                                                                              "Aid drains stockpiles"]}))
    assert auto_mod.claim_parts(reg, "claim here") == ["Aid drains stockpiles"]
    reg = registry_with(cfg, fake=FakeBackend("fake", lambda s, p: {"parts": []}))
    assert auto_mod.claim_parts(reg, "claim here") == ["claim here"]


def test_split_authors_and_scholarly_meta():
    from cardcutter.ingest.source import from_html, split_authors

    assert split_authors("Sandler; Ronald") == ["Ronald Sandler"]
    assert split_authors("Bennett; Joseph R; Maloney; Richard F") == ["Joseph R Bennett", "Richard F Maloney"]
    assert split_authors("John Smith; Jane Doe") == ["John Smith", "Jane Doe"]
    assert split_authors("A Smith, B Jones") == ["A Smith", "B Jones"]
    assert split_authors("Kevin Holden Platt, Contributor") == ["Kevin Holden Platt"]
    assert split_authors("By Jane Doe, Senior Defense Correspondent and Bob Lee") == ["Jane Doe", "Bob Lee"]
    body = " ".join(["Spending on de-extinction could lead to net biodiversity loss for extant species."] * 8)
    html = ('<html><head><title>Spending limited resources | Nature Ecology &amp; Evolution</title>'
            '<meta name="citation_title" content="Spending limited resources on de-extinction">'
            '<meta name="citation_author" content="Bennett, Joseph R.">'
            '<meta name="citation_author" content="Maloney, Richard F.">'
            '<meta name="citation_journal_title" content="Nature Ecology &amp; Evolution">'
            '<meta name="citation_publication_date" content="2017/03/01">'
            '<meta name="citation_doi" content="10.1038/s41559-016-0053">'
            '<meta name="citation_volume" content="1">'
            f'</head><body><article><h1>Spending limited resources</h1><p>{body}</p><p>{body}</p></article></body></html>')
    m = from_html(html, "https://www.nature.com/articles/s41559-016-0053").meta
    assert m.authors == ["Joseph R. Bennett", "Richard F. Maloney"]
    assert m.publication == "Nature Ecology & Evolution"
    assert m.date == "2017-03-01" and m.doi == "10.1038/s41559-016-0053" and m.volume == "1"


def test_write_queries_falls_back_to_the_claim(tmp_path):
    cfg = make_config(tmp_path)
    reg = registry_with(cfg, fake=FakeBackend("fake", lambda s, p: {"plan": "p", "queries": ['"x"', "marine parks enforcement study"]}))
    qs = auto_mod.write_queries(reg, "Marine protected areas lack enforcement", n=4)
    assert qs == ["marine parks enforcement study", "Marine protected areas lack enforcement"]


def test_auto_endpoint_streams_sources_then_cards(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import cardcutter.app.web as web
    from cardcutter.llm.registry import Registry
    from tests.test_web import respond

    cfg = make_config(tmp_path, acronym_rule=False)
    fake = FakeBackend("fake", respond)

    class FakeRegistry(Registry):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._built["fake"] = fake

    monkeypatch.setattr(web, "Registry", FakeRegistry)
    text = ("Jane Q. Doe is a professor of marine policy at Example University.\n\n"
            "Experts warn that disputes over marine protected areas (MPAs) will escalate. Without a treaty, "
            "marine protected areas collapse and biodiversity loss becomes irreversible.")
    src = _source("https://news.example/a", 0.8, text)
    monkeypatch.setattr("cardcutter.search.auto.find_sources",
                        lambda reg, cfg, claim, context="", say=None, **kw: auto_mod.Found(["q"], [src], [], 5))
    c = TestClient(web.create_app(cfg))
    r = c.post("/api/auto", json={"claim": "MPA disputes escalate", "cards": 1, "retrieval": False})
    assert r.status_code == 200, r.text
    for _ in range(200):
        j = c.get(f"/api/jobs/{r.json()['id']}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert j["status"] == "done", j["error"]
    assert j["result"]["sources"][0]["url"] == "https://news.example/a"
    [d] = j["result"]["drafts"]
    assert d["source"]["id"] == src.id and d["card"]["meta"]["doc_id"] == src.id
    # the found source can also be cut by hand
    r2 = c.post("/api/cut", json={"doc_id": src.id, "claim": "MPA disputes escalate", "retrieval": False})
    assert r2.status_code == 200


def test_screen_checks_every_source_and_drops_those_that_dont_argue_the_claim(tmp_path):
    cfg = make_config(tmp_path)
    srcs = [_source("https://against.org/a", 0.9), _source("https://weak.org/b", 0.85),
            _source("https://for.org/c", 0.7), _source("https://late.org/d", 0.5), _source("https://unread.org/e", 0.4)]
    calls = []

    def respond(stage, prompt):
        assert stage == "screen"
        calls.append(prompt.user)
        if len(calls) == 1:
            assert "[3]" in prompt.user and "[4]" not in prompt.user
            return {"results": [{"id": 1, "supports": False, "strength": 1, "reason": "argues bans are respected"},
                                {"id": 2, "supports": True, "strength": 2, "reason": "only mentions it"},
                                {"id": 3, "supports": True, "strength": 5, "reason": "says so directly"}]}
        return {"results": [{"id": 1, "supports": True, "strength": 4, "reason": "argues it"}]}

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    out = auto_mod.screen_sources(reg, "claim", srcs, top=3, limit=4)
    assert len(calls) == 2  # the 4th source is checked in a second batch; the 5th is past the limit
    assert [s.doc.origin for s in out] == ["https://for.org/c", "https://late.org/d", "https://unread.org/e"]
    assert out[0].stance == 5 and "argues the claim 5/5" in out[0].reasons[-1] and out[2].stance is None


def test_find_sources_searches_again_when_too_few_sites_argue_the_claim(tmp_path, monkeypatch):
    from cardcutter.search.providers import SearchHit
    from cardcutter.search.fetch import Fetched

    cfg = make_config(tmp_path)
    cfg.data["search"] = {"rounds": 2, "queries": 2}
    rounds = []

    def respond(stage, prompt):
        if stage == "queries":
            rounds.append(prompt.user)
            return {"plan": "p", "queries": ["first query words", "second query words"] if len(rounds) == 1
                    else ["third query words", "first query words"]}
        return {"results": [{"id": k, "supports": len(rounds) == 2, "strength": 4, "reason": "r"} for k in range(1, 9)]}

    searched = []
    monkeypatch.setattr(auto_mod, "search_all", lambda qs, *a, **k: searched.append(qs) or
                        [SearchHit(f"https://site{len(searched)}.org/{q.split()[0]}") for q in qs])
    monkeypatch.setattr(auto_mod, "fetch_many", lambda hits, *a, **k: [Fetched(h, _source(h.url, 0.5).doc) for h in hits])
    monkeypatch.setattr(auto_mod, "rank_sources", lambda reg, claim, fetched, say=None: [_source(f.hit.url, 0.5) for f in fetched])
    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    found = auto_mod.find_sources(reg, cfg, "claim", want=2)
    assert searched == [["first query words", "second query words"], ["third query words"]]  # repeats dropped
    assert "already run" in rounds[1] and "first query words" in rounds[1]
    assert [s.doc.origin for s in found.sources] == ["https://site2.org/third"]  # round 1's were set aside

    # the second search (more_sources): one round, aimed at the focus, skipping results seen before
    asked = []

    def respond2(stage, prompt):
        if stage == "queries":
            asked.append(prompt.user)
            return {"plan": "p", "queries": ["first query words", "tie query words"]}
        return {"results": [{"id": k, "supports": True, "strength": 4, "reason": "r"} for k in range(1, 9)]}

    reg = registry_with(cfg, fake=FakeBackend("fake", respond2))
    searched.clear()
    found = auto_mod.Found(["first query words"], [], seen={norm_url("https://site1.org/tie")})
    more = auto_mod.more_sources(reg, cfg, "claim", found)("The articles never show X.")
    assert len(asked) == 1 and "The articles never show X." in asked[0] and "- first query words" in asked[0]
    assert searched == [["tie query words"]] and more == []               # its only hit was seen already
    searched.clear()
    found = auto_mod.Found(["first query words"], [])
    more = auto_mod.more_sources(reg, cfg, "claim", found)("The articles never show X.")
    assert [s.doc.origin for s in more] == ["https://site1.org/tie"]
    assert found.queries == ["first query words", "tie query words"] and found.sources == more


def test_sponsored_pages_are_flagged_and_bylines_cleaned(tmp_path):
    from cardcutter.ingest.source import from_html, is_sponsored, split_authors
    from cardcutter.search.rank import quality_bonus

    assert split_authors("Produced by") == [] and split_authors("By Jane Doe") == ["Jane Doe"]
    assert is_sponsored("<html></html>", "https://www.geekwire.com/contributor-content/some-story/")
    assert is_sponsored('<meta name="brandedcontent" content="true">', "https://www.nature.com/articles/d42473-024-1")
    assert not is_sponsored('<meta name="description" content="sponsored research">', "https://news.example.edu/story")
    body = " ".join(["Gene editing could restore lost genetic diversity to endangered pigeons."] * 8)
    html = (f'<html><head><meta name="brandedcontent" content="true"><title>Gene editing</title></head>'
            f'<body><article><p>{body}</p><p>{body}</p></article></body></html>')
    doc = from_html(html, "https://www.nature.com/articles/d42473-024-00109-z")
    assert doc.flags == ["sponsored"]
    bonus, why = quality_bonus(doc, SearchHit(doc.origin))
    assert bonus < 0 and "sponsored content" in why
