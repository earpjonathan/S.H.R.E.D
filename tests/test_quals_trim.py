"""Quals from page data (journal affiliation tags, author boxes, schema.org), quals matched to
authors, cite layout, and the highlight trim / repeat rules."""
from __future__ import annotations

from cardcutter.card import FText
from cardcutter.cite.extract import align_quals
from cardcutter.cite.format import build_cite
from cardcutter.cite.meta import CiteMeta
from cardcutter.cutter.pipeline import cut_window, read_aloud
from cardcutter.eval.run import candidate_scores
from cardcutter.cutter.rules import drop_repeats, drop_stranded, keep_negators, tidy_spaces
from cardcutter.ingest.source import author_info, clean_affiliation, from_html
from cardcutter.tokenize import tokenize
from tests.fakes import FakeBackend, make_config, registry_with

BODY = " ".join(["Spending on de-extinction could lead to net biodiversity loss for extant species."] * 8)


def test_clean_affiliation_keeps_the_organisation():
    assert clean_affiliation("Department of Biology, Carleton University, Ottawa, Canada") == \
        "Department of Biology, Carleton University"
    assert clean_affiliation("New South Wales Office of Environment and Heritage, Sydney, New South Wales 2000, "
                             "Australia") == "New South Wales Office of Environment and Heritage"
    assert clean_affiliation("Department of Biology, Carleton University, 1125 Colonel By Drive, Ottawa K1S 5B6") == \
        "Department of Biology, Carleton University"


def test_journal_tags_give_each_author_their_institution():
    html = ('<html><head>'
            '<meta name="citation_author" content="Bennett, Joseph R.">'
            '<meta name="citation_author_institution" content="Department of Biology, Carleton University, Ottawa, Canada">'
            '<meta name="citation_author" content="Maloney, Richard F.">'
            '<meta name="citation_author" content="Steeves, Tammy E.">'
            '<meta name="citation_author_institution" content="School of Biological Sciences, University of Canterbury, Christchurch, New Zealand">'
            f'</head><body><article><p>{BODY}</p><p>{BODY}</p></article></body></html>')
    m = from_html(html, "https://www.nature.com/articles/x").meta
    assert m.authors == ["Joseph R. Bennett", "Richard F. Maloney", "Tammy E. Steeves"]
    assert m.quals == ["Department of Biology, Carleton University", "",
                       "School of Biological Sciences, University of Canterbury"]


def test_author_boxes_and_schema_org_reach_the_byline_but_scripts_do_not():
    html = ('<html><head><script type="application/ld+json">{"@type": "NewsArticle", "author": [{"@type": "Person", '
            '"name": "Jack Knudson", "jobTitle": "Associate Editor", "url": "https://example.com/people/jack"}]}</script>'
            '</head><body><article><p>' + BODY + '</p></article>'
            '<div class="author-bio">Jack Knudson is an Associate Editor for Discover Magazine.'
            '<script>var x = "tracking";</script></div>'
            '<a rel="author" href="/profiles/jack-knudson">Jack Knudson</a>'
            '<div class="author-share">Share on Facebook Share on Twitter</div></body></html>')
    byline, pages = author_info(html, "https://example.com/story", ["Jack Knudson"])
    assert "Associate Editor for Discover Magazine" in byline and "Jack Knudson: Associate Editor" in byline
    assert "tracking" not in byline
    assert "https://example.com/profiles/jack-knudson" in pages and "https://example.com/people/jack" in pages


def test_quals_are_matched_to_authors_by_name_not_position():
    hay = "christopher lean research fellow in philosophy andrew james latham postdoctoral fellow, philosophy, aarhus university"
    out = align_quals(["Andrew James Latham", "Annie Sandrussi", "Christopher Lean"],
                      ["Christopher Lean", "Andrew James Latham"],
                      ["Research Fellow in Philosophy", "Postdoctoral Fellow, Philosophy, Aarhus University"], hay)
    assert out == ["Postdoctoral Fellow, Philosophy, Aarhus University", "", "Research Fellow in Philosophy"]
    assert align_quals(["A B"], ["A B"], ["Professor of Made-Up Studies"], hay) == [""]  # not in the page: dropped


def test_cite_keeps_each_qual_with_its_author_and_uses_et_al():
    m = CiteMeta(authors=["Joseph R. Bennett", "Richard F. Maloney", "Tammy E. Steeves", "Hugh P. Possingham"],
                 quals=["Carleton University", "", "University of Canterbury", "University of Queensland"],
                 date="2017-03-01", title="Spending", publication="Nature Ecology & Evolution", url="https://x")
    text = build_cite(m).cite.text
    assert text.startswith("Joseph R. Bennett et al. 17, Carleton University, Richard F. Maloney, Tammy E. Steeves, "
                           "University of Canterbury, Hugh P. Possingham, ")
    assert "University of Queensland" not in text  # quals for the first three authors only


def test_negators_come_back_and_stranded_function_words_go():
    toks = tokenize("funding would not lead to fewer species being conserved at all")
    hl_idx = list(range(len(toks)))
    keep = {0, 3, 5, 6}          # dropped "not" between kept words
    assert keep_negators(toks, hl_idx, keep) == 1 and 2 in keep
    keep = {0, 5, 6, 9}          # "at" (9) left alone: 8 and 10 dropped
    assert drop_stranded(toks, hl_idx, keep) == 1 and 9 not in keep


def test_a_negator_follows_the_word_it_applies_to():
    toks = tokenize("Putin is not budging and seems confident of victory")
    hl_idx = list(range(len(toks)))
    keep = {0, 5, 6}             # "Putin seems confident": "not" negates the dropped "budging"
    assert keep_negators(toks, hl_idx, keep) == 0 and 2 not in keep
    keep = {0, 2, 5, 6}          # "Putin not seems confident": the negated word comes back
    assert keep_negators(toks, hl_idx, keep) == 1 and keep == {0, 2, 3, 5, 6}
    toks = tokenize("the U.S. may not actually access the resources")
    keep = {1, 3, 4}             # "U.S. not actually" -> "U.S. not actually access"
    assert keep_negators(toks, list(range(len(toks))), keep) == 1 and 5 in keep
    toks = tokenize("British have to deliver none. Jets are old")
    keep = {0, 3, 5}             # "deliver none." has no word after it: it comes back by its verb
    assert keep_negators(toks, list(range(len(toks))), keep) == 1 and 4 in keep


def test_drop_repeats_clears_a_sentence_that_restates_an_earlier_one():
    text = ("Funding resurrected species causes substantial sacrifices in extant species conservation. "
            "Potential sacrifices in conservation of extant species.")
    toks = tokenize(text)
    hl = [True] * len(text)
    assert drop_repeats(text, toks, hl) == 1
    assert read_aloud(text, hl).startswith("Funding resurrected") and "Potential" not in read_aloud(text, hl)


def test_tidy_spaces_removes_stray_highlighted_gaps():
    text = "a b  c"
    hl = [True, True, False, True, True, True]   # "a " highlighted, "b" not, "  c" highlighted
    tidy_spaces(text, hl)
    assert hl == [True, False, False, False, False, True]


def test_trim_pass_only_deletes_and_falls_back_when_it_cuts_too_deep(tmp_path):
    source = ("The collapse of coastal fisheries would cause widespread food insecurity across poor nations "
              "that depend on them, and that would not be reversible for decades.")
    cfg = make_config(tmp_path, trim_highlight=True, emphasis=False, tag=False)
    every = [{"start": 0, "end": len(source.split()) - 1}]
    answers = {"underline": {"plan": "p", "underline": every},
               "highlight": {"plan": "p", "highlight": every, "partial": []}}
    trims = iter([{"plan": "collapse fisheries cause food insecurity",
                   "keep": [1, 4, 6, 8, 9, 999]},   # 999 isn't highlighted: ignored
                  {"plan": "x", "keep": [1]}])      # too deep: original kept

    def respond(stage, prompt):
        if stage == "trim":
            return next(trims)
        return answers[stage]

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    res = cut_window(reg, cfg, source, "claim", do_readback=False)
    said = res.body.read_aloud()
    assert said.split() == ["collapse", "fisheries", "cause", "food", "insecurity"]
    assert any(log.stage == "trim" and log.fixes == 1 for log in res.logs)
    res = cut_window(reg, cfg, source, "claim", do_readback=False)
    assert len(res.body.read_aloud().split()) > 20  # fell back to the untrimmed highlight


def test_readback_tries_the_untrimmed_highlight_before_recutting(tmp_path):
    source = ("The collapse of coastal fisheries would cause widespread food insecurity across poor nations "
              "that depend on them, and that would not be reversible for decades.")
    cfg = make_config(tmp_path, trim_highlight=True, emphasis=False, tag=False)
    cfg.data["readback"] = {"enabled": True, "min_score": 3, "max_retries": 1, "reunderline": True,
                            "untrimmed_fallback": True}
    every = [{"start": 0, "end": len(source.split()) - 1}]
    calls = []

    def respond(stage, prompt):
        calls.append(stage)
        if stage == "underline":
            return {"plan": "p", "underline": every}
        if stage == "highlight":
            return {"plan": "p", "highlight": every, "partial": []}
        if stage == "trim":
            return {"plan": "collapse fisheries cause food insecurity", "keep": [1, 4, 6, 8, 9]}
        if stage == "readback":   # trimmed version fails, untrimmed passes
            short = "collapse fisheries cause food insecurity" in prompt.user and "widespread" not in prompt.user
            return {"grammatical": 2 if short else 4, "coherent": 2 if short else 4, "proves_tag": 4,
                    "power_tag": False, "feedback": "fragmented" if short else "none"}
        raise AssertionError(stage)

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    res = cut_window(reg, cfg, source, "claim")
    assert calls == ["underline", "highlight", "trim", "readback", "readback"]  # no re-cut
    assert "widespread" in res.body.read_aloud() and res.readback["min"] == 4
    assert not any("below threshold" in w for w in res.warnings)
    # both versions the judge heard are kept for eval, and the scorer finds the one used
    assert [c["label"] for c in res.candidates] == ["read-back", "read-back (untrimmed)"]
    gold = FText(source)
    gold.marks["underline"] = [not ch.isspace() for ch in source]
    gold.marks["highlight"] = ["yellow" if not ch.isspace() else None for ch in source]
    scored = [candidate_scores(c, gold, res) for c in res.candidates]
    assert [s["chosen"] for s in scored] == [False, True]
    assert scored[1]["highlight_f1"] > scored[0]["highlight_f1"] and scored[1]["min"] == 4


def test_from_url_falls_back_when_the_first_download_is_a_stub(monkeypatch):
    import trafilatura

    import cardcutter.search.fetch as fetch_mod
    from cardcutter.ingest import source as src

    stub = "<html><body><p>Please enable JavaScript to continue to this site.</p></body></html>"
    full = f"<html><body><article><p>{BODY}</p><p>{BODY}</p><p>{BODY}</p></article></body></html>"
    monkeypatch.setattr(trafilatura, "fetch_url", lambda url: stub)
    monkeypatch.setattr(fetch_mod, "_download", lambda url, cache, timeout: (full.encode(), "text/html", url))
    doc = src.from_url("https://pmc.example.org/articles/1/")
    assert doc.words > 60 and "biodiversity" in doc.text  # the full page, not the stub
    monkeypatch.setattr(fetch_mod, "_download", lambda url, cache, timeout: (stub.encode(), "text/html", url))
    import pytest
    with pytest.raises(ValueError, match="could only read"):
        src.from_url("https://pmc.example.org/articles/1/")


def test_crossref_title_wins_over_junk_pdf_metadata(monkeypatch):
    from cardcutter.cite import extract as ex
    from cardcutter.ingest.source import Document

    doc = Document(["Body text " * 30], CiteMeta(title="JEC PDF 2 ISSUE 4", doi="10.1/x", sources=["pdf metadata"]), "pdf")
    monkeypatch.setattr(ex, "crossref", lambda doi: CiteMeta(title="Empowering biodiversity", publication="J Ecol Cons",
                                                             date="2025-05-02", sources=["crossref"]))
    monkeypatch.setattr(ex, "openalex_affiliations", lambda doi: [])
    meta, _ = ex.resolve_meta(doc, None, extract_quals=False)
    assert meta.title == "Empowering biodiversity" and meta.publication == "J Ecol Cons"


def test_without_the_untrimmed_fallback_the_trim_is_kept_and_flagged(tmp_path):
    source = ("The collapse of coastal fisheries would cause widespread food insecurity across poor nations "
              "that depend on them, and that would not be reversible for decades.")
    cfg = make_config(tmp_path, trim_highlight=True, emphasis=False, tag=False)
    cfg.data["readback"] = {"enabled": True, "min_score": 3, "max_retries": 0, "untrimmed_fallback": False}
    every = [{"start": 0, "end": len(source.split()) - 1}]
    calls = []

    def respond(stage, prompt):
        calls.append(stage)
        if stage == "underline":
            return {"plan": "p", "underline": every}
        if stage == "highlight":
            return {"plan": "p", "highlight": every, "partial": []}
        if stage == "trim":
            return {"plan": "collapse fisheries cause food insecurity", "keep": [1, 4, 6, 8, 9]}
        if stage == "readback":
            return {"grammatical": 2, "coherent": 2, "proves_tag": 4, "power_tag": False, "feedback": "fragmented"}
        raise AssertionError(stage)

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    res = cut_window(reg, cfg, source, "claim")
    assert calls == ["underline", "highlight", "trim", "readback"]
    assert res.body.read_aloud().split() == ["collapse", "fisheries", "cause", "food", "insecurity"]
    assert any("below threshold" in w for w in res.warnings)


def test_quals_keep_titles_and_drop_bio_run_ons():
    from cardcutter.cite.extract import tidy_qual

    q = ("a former network correspondent who covered Khrushchev, currently a non-resident senior fellow at "
         "Brookings, is author of the soon-to-be published “A DIFFERENT RUSSIA,” his reporting on their relationship.")
    assert tidy_qual(q) == "former network correspondent who covered Khrushchev, non-resident senior fellow at Brookings"
    two = "Lecturer in Politics at the University of Essex, PhD in Politics from Sheffield Hallam University"
    assert tidy_qual(two) == two
    box = ("Isaac Seitz graduated from Patrick Henry College’s Strategic Intelligence and National Security "
           "program. He has also studied Russian at Middlebury Language Schools and has worked as an analyst")
    assert tidy_qual(box, name="Isaac Seitz") == ("graduated from Patrick Henry College’s Strategic Intelligence "
                                                  "and National Security program")
    assert tidy_qual("Jane Doe is a senior fellow at the U.S. Naval War College. She writes on Russia.",
                     name="Jane Doe") == "senior fellow at the U.S. Naval War College"
    assert tidy_qual("Professor at the University of St. Andrews", name="Ann Smith") == \
        "Professor at the University of St. Andrews"


def test_repair_adds_back_only_dropped_words_and_their_gaps(tmp_path):
    source = ("FMS-related obligations made up 9.3 percent of total defense contract obligations in the "
              "years the report reviewed, according to officials.")
    cfg = make_config(tmp_path, trim_highlight=True, trim_repair=True, emphasis=False, tag=False)
    every = [{"start": 0, "end": len(source.split()) - 1}]
    seen = []

    def respond(stage, prompt):
        if stage == "trim":
            return {"plan": "p", "keep": [0, 1, 4, 5, 8, 9, 10]}
        if stage == "repair":
            seen.append(prompt.user)
            return {"plan": "p", "add": [2, 3, 1, 999, 2]}  # 1 is already kept, 999 unknown, 2 repeated
        return {"underline": {"plan": "p", "underline": every},
                "highlight": {"plan": "p", "highlight": every, "partial": []}}[stage]

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    res = cut_window(reg, cfg, source, "FMS supports the industrial base", do_readback=False)
    assert "0:*FMS-related 1:*obligations 2:made 3:up 4:*9.3" in seen[0]
    assert res.body.read_aloud().split() == ["FMS-related", "obligations", "made", "up", "9.3", "percent",
                                             "defense", "contract", "obligations"]
    assert "obligations made up 9.3" in res.body.read_aloud()  # the gaps came back too
    assert any(log.stage == "repair" and log.fixes == 3 for log in res.logs)


def test_a_byline_label_is_replaced_by_the_outlet_and_an_org_stays_whole():
    from cardcutter.cite.format import build_cite
    from cardcutter.cite.meta import CiteMeta

    cite = lambda a, pub: build_cite(CiteMeta(authors=[a], publication=pub, date="2026-10-19")).cite.text  # noqa: E731
    assert cite("U S News Staff", "U.S. News & World Report").startswith("U.S. News & World Report 26,")
    assert cite("Lansing Institute", "lansinginstitute.org").startswith("Lansing Institute 26,")
    assert cite("Paul Kirby", "BBC").startswith("Paul Kirby 26,")


def test_bridge_gaps_fills_short_underlined_gaps_inside_a_sentence():
    from cardcutter.cutter.rules import bridge_gaps
    from cardcutter.tokenize import tokenize

    text = "Yet Ukraine shows no signs of capitulating soon. Neither side can rely on these long strikes. The end."
    toks = tokenize(text)
    ul = [True] * len(text)
    hl = [False] * len(text)

    def mark(*words):
        for w in words:
            i = text.index(w)
            for c in range(i, i + len(w)):
                hl[c] = True

    mark("Ukraine shows no", "capitulating", "Neither side", "rely", "strikes.", "The end")
    n = bridge_gaps(text, toks, hl, ul, 2)
    read = " ".join(t.text for t in toks if any(hl[t.start:t.end]))
    assert "no signs of capitulating" in read          # 2-word gap filled
    assert "Neither side can rely" in read             # 1-word gap filled
    assert "rely on these long strikes" not in read    # 3-word gap left alone
    assert "strikes. The" in read and n == 2           # nothing bridged across a sentence end
    hl2 = [False] * len(text)
    hl, ul2 = hl2, [c != text.index("can") for c in range(len(text))]  # "can" not underlined
    mark("Neither side", "rely")
    assert bridge_gaps(text, toks, hl, ul2, 2) == 0


def test_a_negator_whose_word_is_unread_is_completed_or_dropped():
    from cardcutter.cutter.rules import fix_dangling_negators
    from cardcutter.tokenize import tokenize

    text = ("This is strategic neutralization: the goal isn't to exhaust Russia, but it puts Russia at a "
            "disadvantage. Russia has not yet won the war. The U.S. may not actually be able to access it. "
            "Kyiv did not.")
    toks = tokenize(text)
    ul = [True] * len(text)
    gap = text.index("to exhaust Russia, but it")
    ul[gap:gap + len("to exhaust Russia, but it")] = [False] * len("to exhaust Russia, but it")
    hl = [False] * len(text)
    for w in ("strategic neutralization:", "goal isn't", "Russia at a", "disadvantage.", "Russia has not",
              "won the war.", "U.S. may not actually", "access it.", "Kyiv did not."):
        i = text.index(w)
        hl[i:i + len(w)] = [True] * len(w)
    assert fix_dangling_negators(text, toks, hl, ul) == 2
    read = " ".join(t.text for t in toks if any(hl[t.start:t.end]))
    assert "isn't" not in read and "goal" not in read            # "goal isn't Russia at" would flip it
    assert "Russia has not won the war." in read                 # "yet" is skipped: the negated word is read
    assert "U.S. may not actually be able access it." in read    # underlined: completed, not dropped
    assert read.endswith("Kyiv did not.")                        # nothing follows: left alone


def test_tags_lose_pointers_to_people_the_article_never_names():
    from cardcutter.cutter.pipeline import strip_stray_reference

    art = "Oliveira writes that Ukraine turned conventional inferiority into a source of strength."
    assert strip_stray_reference("Asymmetry turns inferiority into strength---that's Frolova.", art) == \
        "Asymmetry turns inferiority into strength."
    assert strip_stray_reference("Asymmetry works---that's Oliveira.", art) == "Asymmetry works---that's Oliveira."
    assert strip_stray_reference("Asymmetry works.", art) == "Asymmetry works."


def test_boxes_are_few_short_and_only_on_read_words():
    from cardcutter.cutter.rules import cap_emphasis
    toks = tokenize("Russia has the largest nuclear arsenal and 1,500 tactical nuclear weapons for battlefield use.")
    read = set(range(0, 13))                                   # "use." is not read
    ranges = [(0, 5), (7, 11), (12, 13), (2, 2)]
    out = cap_emphasis(toks, ranges, read, max_boxes=2, max_words=2)
    words = [" ".join(t.text for t in toks[a:b + 1]) for a, b in out]
    assert words == ["Russia", "1,500 tactical nuclear"]  # no box ends on "has"; a number allows 3 words; 2 boxes at most
    assert cap_emphasis(toks, [(2, 3)], read, max_boxes=3) == [(3, 3)]  # no box starts on "the"
    assert cap_emphasis(toks, [(13, 13)], read, max_boxes=3) == []      # unread words get no box


def test_smooth_adds_a_missing_subject_and_underlines_it(tmp_path):
    source = "Russia also possesses roughly 2,000 tactical nuclear weapons designed for battlefield use."
    cfg = make_config(tmp_path, smooth=True, emphasis=False, tag=False)
    seen = []

    def respond(stage, prompt):
        if stage == "smooth":
            seen.append(prompt.user)
            return {"plan": "p", "add": [0, 2, 99]}          # 2 is already read, 99 is unknown
        return {"underline": {"plan": "p", "underline": [{"start": 2, "end": 7}]},
                "highlight": {"plan": "p", "highlight": [{"start": 2, "end": 2}, {"start": 4, "end": 7}],
                              "partial": []}}[stage]

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    res = cut_window(reg, cfg, source, "Russia has tactical nukes", do_readback=False)
    assert "0:Russia 1:also 2:*possesses" in seen[0]
    assert res.body.read_aloud().startswith("Russia possesses 2,000 tactical")
    assert all(res.body.marks["underline"][i] for i in range(len("Russia")))  # the added subject is underlined
    assert any(log.stage == "smooth" and log.fixes == 2 for log in res.logs)


def test_a_highlight_far_over_target_is_cut_back_by_whole_pieces(tmp_path):
    source = " ".join(f"Point {i} matters because it causes harm." for i in range(12))
    cfg = make_config(tmp_path, trim_highlight=False, trim_if_over=1.5, trim_over_mode="words", emphasis=False,
                      tag=False)
    every = [{"start": 0, "end": len(source.split()) - 1}]
    seen = []

    def respond(stage, prompt):
        if stage == "trim":
            seen.append(prompt.user)
            return {"plan": "p", "keep": list(range(0, 14))}
        return {"underline": {"plan": "p", "underline": every},
                "highlight": {"plan": "p", "highlight": every, "partial": []}}[stage]

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    res = cut_window(reg, cfg, source, "Points cause harm", do_readback=False)
    assert seen and "too long to read" in seen[0]
    assert len(res.body.read_aloud().split()) == 14


def test_neighboring_highlighted_words_have_no_gap_between_them():
    from cardcutter.cutter.rules import join_adjacent
    text = "Russia could increase its presence near Finland soon."
    toks = tokenize(text)
    hl = [False] * len(text)
    ul = [False] * len(text)
    for i in (0, 1, 2, 3, 4, 5, 6):                       # two ranges: 0-4 and 5-6, the space between unmarked
        for c in range(toks[i].start, toks[i].end):
            hl[c] = ul[c] = True
    for c in range(toks[0].start, toks[4].end):
        hl[c] = ul[c] = True
    gap = toks[4].end
    hl[gap] = ul[gap] = False
    assert join_adjacent(text, toks, hl, ul) == 2 and hl[gap] and ul[gap]   # "presence near", "near Finland"
    assert not hl[toks[6].end]                             # the space after the last read word stays plain


def test_neighboring_underlined_words_have_an_unbroken_line():
    from cardcutter.cutter.rules import join_adjacent
    text = "post-crisis efforts were not responsible"
    toks = tokenize(text)
    ul = [not ch.isspace() for ch in text]                 # every word underlined, no space underlined
    hl = [False] * len(text)
    assert join_adjacent(text, toks, hl, ul) == 4 and all(ul) and not any(hl)


def test_footnote_numbers_are_not_read_aloud():
    from cardcutter.cutter.rules import unread_footnotes
    text = "could run out of munitions within weeks.2 Then “keep pace with conflict.”4 in 2024, 9.3 percent, 2,000 shells."
    toks = tokenize(text)
    hl = [True] * len(text)
    assert unread_footnotes(text, toks, hl) == 2
    ft = FText(text)
    ft.marks["highlight"] = ["y" if h else None for h in hl]
    assert "weeks. Then" in ft.read_aloud() and "conflict.” in 2024, 9.3 percent, 2,000 shells." in ft.read_aloud()


def test_a_card_starts_and_ends_at_its_marked_paragraphs(tmp_path):
    paras = ["The scene was quiet that morning in the village.",
             "China controls most rare-earth refining in the world today.",
             "That gives Beijing leverage over every Western arms maker.",
             "The reporter then walked back to the car."]
    source = "\n".join(paras)
    toks = tokenize(source)
    a = next(t.index for t in toks if t.text == "China")
    b = next(t.index for t in toks if t.text == "leverage")
    marks = [{"start": a, "end": a + 4}, {"start": b - 2, "end": b}]
    cfg = make_config(tmp_path, emphasis=False, tag=False)

    def respond(stage, prompt):
        return {"underline": {"plan": "p", "underline": marks},
                "highlight": {"plan": "p", "highlight": marks, "partial": []}}[stage]

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    res = cut_window(reg, cfg, source, "China has leverage", do_readback=False)
    assert res.body.text == paras[1] + "\n" + paras[2]
    assert res.offset == len(paras[0]) + 1 and source[res.offset:].startswith(res.body.text)
    assert res.body.read_aloud().startswith("China controls most rare-earth refining")

    keep = make_config(tmp_path, emphasis=False, tag=False, drop_unmarked_edges=False)
    res = cut_window(registry_with(keep, fake=FakeBackend("fake", respond)), keep, source, "China has leverage",
                     do_readback=False)
    assert res.body.text == source and res.offset == 0


def test_a_piece_starting_with_a_helping_verb_gets_its_short_subject_back():
    from cardcutter.cutter.rules import restore_subjects
    text = ("Davis said so. The war in Ukraine has exposed Europe's weaknesses. The U.S. could run out "
            "of shells. Experts at the Center for Strategic and International Studies say it could fail. "
            "Harrel concludes that the gambit failed.")
    toks = tokenize(text)
    hl = [False] * len(text)
    ul = [False] * len(text)
    for phrase in ("has exposed Europe's weaknesses.", "could run out of shells.", "could fail.",
                   "concludes that the gambit failed."):
        a = text.index(phrase)
        for c in range(a, a + len(phrase)):
            hl[c] = ul[c] = True
    assert restore_subjects(text, toks, hl, ul) == 3          # the third subject is too long to read
    ft = FText(text)
    ft.marks["highlight"] = ["y" if h else None for h in hl]
    assert ft.read_aloud() == ("The war in Ukraine has exposed Europe's weaknesses. The U.S. could run out of shells. "
                               "could fail. Harrel concludes that the gambit failed.")
    assert ul[text.index("The war")]


def test_a_card_starts_at_a_sentence_and_ends_soon_after_its_last_mark():
    from cardcutter.cutter.rules import marked_edges
    filler = " ".join(["Other states may follow suit for many reasons."] * 6)       # 48 words
    text = ("operations could increase reliability in a crisis. Automation can help leaders. "
            "Poor automation could render the U.S. systems opaque to users. " + filler)
    ul = [False] * len(text)
    s = text.index("Poor automation")
    for c in range(s, text.index(" to users.") + len(" to users.")):
        ul[c] = True
    a, b = marked_edges(text, ul)
    assert text[a:b] == "Automation can help leaders. Poor automation could render the U.S. systems opaque to users."
    short = "Leaders said so. It works well. " + "Poor automation could render systems opaque."
    ul = [False] * len(short)
    for c in range(short.index("Poor"), len(short)):
        ul[c] = True
    assert marked_edges(short, ul) == (0, len(short))          # a short lead-in and no tail: kept whole


def test_a_highlight_far_over_target_keeps_its_most_needed_sentences_whole(tmp_path):
    sents = [f"Point {i} matters because it causes real harm to people." for i in range(10)]   # 10 words each
    source = " ".join(sents)
    cfg = make_config(tmp_path, trim_highlight=False, trim_if_over=1.5, emphasis=False, tag=False)
    every = [{"start": 0, "end": len(source.split()) - 1}]
    seen = []

    def respond(stage, prompt):
        if stage == "trim":
            seen.append(prompt.user)
            return {"plan": "p", "order": [7, 2, 2, 99, 5]}       # a repeat and an unknown number are ignored
        return {"underline": {"plan": "p", "underline": every},
                "highlight": {"plan": "p", "highlight": every, "partial": []}}[stage]

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    res = cut_window(reg, cfg, source, "Points cause harm", do_readback=False)
    assert seen and "7: Point 7 matters because it causes real harm to people. (10 words)" in seen[0]
    # target 13 words (test density) x slack 1.2: the top-ranked sentence fits, a second would not
    assert res.body.read_aloud() == "Point 7 matters because it causes real harm to people."


def test_the_headline_is_the_title_when_the_seo_title_misses_the_url():
    from cardcutter.ingest.source import headline_title
    html = ('<title>surviving nuclear war</title><h1 class="entry-title">Resilience and Resistance: The importance '
            'of envisaging the end of the world (and after)</h1>')
    url = "https://example.org/2024/05/01/resilience-and-resistance-the-importance-of-envisaging-the-end-of-the-world-and-after/"
    assert headline_title("surviving nuclear war", html, url).startswith("Resilience and Resistance: The importance")
    same = "<h1>Russia Is Rebuilding Its Army</h1>"
    assert headline_title("Russia is rebuilding its army fast", same, "https://x.com/news/russia-rebuilding-army-fast") \
        == "Russia is rebuilding its army fast"
    assert headline_title("A title", "<h1>Menu</h1>", "https://x.com/p/12345") == "A title"


def test_one_openalex_affiliation_for_every_author_is_only_a_fallback(monkeypatch, tmp_path):
    from cardcutter.cite import extract as ex
    from cardcutter.ingest.source import Document

    text = ("1 Michael Horowitz is Professor of Political Science at the University of Pennsylvania. "
            "Paul Scharre is Senior Fellow at the Center for a New American Security. ") + "Body text " * 30
    names = ["Michael Horowitz", "Paul Scharre"]
    doc = Document([text], CiteMeta(authors=names, doi="10.48550/arxiv.1", sources=["openalex"]), "pdf")
    monkeypatch.setattr(ex, "crossref", lambda doi: CiteMeta())
    garbled = ["Perry World House at the University of Pennsylvania. Paul"] * 2
    monkeypatch.setattr(ex, "openalex_affiliations", lambda doi: garbled)
    cfg = make_config(tmp_path)
    quals = ["Professor of Political Science at the University of Pennsylvania",
             "Senior Fellow at the Center for a New American Security"]
    reg = registry_with(cfg, fake=FakeBackend("fake", lambda stage, p: {"authors": names, "quals": quals,
                                                                        "date": "", "publication": ""}))
    meta, _ = ex.resolve_meta(doc, reg)
    assert meta.quals == quals
    reg = registry_with(cfg, fake=FakeBackend("fake", lambda stage, p: {"authors": names, "quals": ["", ""],
                                                                        "date": "", "publication": ""}))
    meta, notes = ex.resolve_meta(doc, reg)
    assert meta.quals == garbled and any("not found in the paper" in n for n in notes)


def test_a_report_page_cites_its_publisher_and_its_update_date():
    body = " ".join(["Taiwan buys most of its arms from the United States through Foreign Military Sales."] * 30)
    html = ('<html><head><title>Taiwan: Defense and Military Issues</title><script type="application/ld+json">'
            '{"@type": "Report", "headline": "Taiwan: Defense and Military Issues", "datePublished": "2023-08-24",'
            ' "publisher": {"@type": "Organization", "name": "Congressional Research Service"}}</script></head>'
            '<body><div class="meta">Updated June 18, 2026 (IF12481)</div><article><h1>Taiwan: Defense and '
            f'Military Issues</h1><p>{body}</p><p>{body}</p></article></body></html>')
    m = from_html(html, "https://www.everycrsreport.com/reports/IF12481.html").meta
    assert m.date == "2026-06-18"
    assert m.publication == "Congressional Research Service"


def test_one_word_gaps_in_the_underline_are_filled_within_a_sentence():
    from cardcutter.cutter.rules import fill_underline_gaps
    text = "It has an estimated range of 800 km. The drone is cheap and new."
    toks = tokenize(text)
    ul = [False] * len(text)
    for w in ("It", "an", "range", "of", "800", "km.", "drone", "cheap"):
        t = next(t for t in toks if t.text == w)
        for c in range(t.start, t.end):
            ul[c] = True
    assert fill_underline_gaps(text, toks, ul) == 3                 # has, estimated, and "is" between drone and cheap
    under = "".join(ch if u else " " for ch, u in zip(text, ul))
    assert "It has an estimated range of 800 km." in under
    assert "drone is cheap" in under and "The" not in under      # never across "km. The"


def test_a_window_after_a_page_break_starts_at_its_next_sentence():
    from cardcutter.cutter.rules import marked_edges, starts_mid_sentence
    from cardcutter.ingest.source import clean_paragraph
    before = "Long-range strikes hold Russian bases at risk, which supports the overall balance of the\n"
    assert starts_mid_sentence(before)
    assert not starts_mid_sentence("Firepower and Military Mass\n") and not starts_mid_sentence("It ended.\n")
    text = "European deterrence effect, as it suspends the balance. Europe is within reach of Russia."
    ul = [False] * len(text)
    for c in range(text.index("Europe is"), len(text)):
        ul[c] = True
    assert text[slice(*marked_edges(text, ul, mid_sentence=True))] == "Europe is within reach of Russia."
    assert marked_edges(text, ul) == (0, len(text))
    assert clean_paragraph("mutual vulner\xad ability and long\xadrange") == "mutual vulnerability and longrange"


def test_a_read_piece_one_word_short_of_its_sentence_end_is_finished():
    from cardcutter.cutter.rules import finish_sentences
    text = ("Putin's core objective is to fracture the NATO alliance. Russia will escalate. It may fail soon. "
            "Taiwan buys through Foreign Military Sales (FMS).")
    toks = tokenize(text)
    hl = [False] * len(text)
    ul = [False] * len(text)
    for phrase in ("Putin's core objective is to fracture the NATO", "It may", "Taiwan buys through Foreign Military Sales"):
        a = text.index(phrase)
        for c in range(a, a + len(phrase)):
            hl[c] = ul[c] = True
    assert finish_sentences(text, toks, hl, ul) == 1            # "It may" is too short a piece to finish
    ft = FText(text)
    ft.marks["highlight"] = ["y" if h else None for h in hl]
    assert ft.read_aloud() == ("Putin's core objective is to fracture the NATO alliance. It may "
                               "Taiwan buys through Foreign Military Sales")


def test_the_box_cap_also_counts_boxed_words():
    from cardcutter.cutter.rules import cap_emphasis
    text = "Russia will escalate against NATO member states in a limited military incursion soon"
    toks = tokenize(text)
    allowed = set(range(len(toks)))
    boxes = cap_emphasis(toks, [(0, 0), (2, 2), (4, 5), (10, 11)], allowed, max_boxes=6, max_total=4)
    assert boxes == [(0, 0), (2, 2), (4, 5)]
    boxes = cap_emphasis(toks, [(0, 0), (2, 2), (4, 5), (12, 12)], allowed, max_boxes=6, max_total=3)
    assert boxes == [(0, 0), (2, 2), (12, 12)]                   # the two-word box doesn't fit: skipped, not cut


def test_smooth_runs_after_the_trim_and_only_sees_what_is_still_read(tmp_path):
    sents = [f"Point {i} matters because it causes real harm to people." for i in range(10)]
    source = " ".join(sents)
    cfg = make_config(tmp_path, trim_highlight=False, trim_if_over=1.5, smooth=True, emphasis=False, tag=False)
    every = [{"start": 0, "end": len(source.split()) - 1}]
    order, smooth_views = [], []

    def respond(stage, prompt):
        order.append(stage)
        if stage == "trim":
            return {"plan": "p", "order": [7]}
        if stage == "smooth":
            smooth_views.append(prompt.user)
            return {"plan": "p", "add": []}
        return {"underline": {"plan": "p", "underline": every},
                "highlight": {"plan": "p", "highlight": every, "partial": []}}[stage]

    cut_window(registry_with(cfg, fake=FakeBackend("fake", respond)), cfg, source, "Points cause harm", do_readback=False)
    assert order == ["underline", "highlight", "trim", "smooth"]
    assert "*Point" in smooth_views[0] and smooth_views[0].count("*Point") == 1


def test_footnoted_sentence_ends_and_edges_agree_with_the_integrity_check():
    from cardcutter.cutter.rules import marked_edges
    from cardcutter.tokenize import ends_sentence, sentence_starts

    # a footnote number after a stop still ends the sentence; a decimal doesn't (Heritage PDF, 2026-11-12)
    t = "Taiwan must modernize its reserve forces.9 If Taiwan does not, it fails. Growth was 3.5 Percent."
    starts = sentence_starts(t)
    assert t.index("If Taiwan") in starts and t.index("Percent") not in starts
    assert ends_sentence("reserve forces.9") and not ends_sentence("grew 3.5") and ends_sentence("in 1990.")
    # marked_edges never picks a start the integrity check would reject
    text = "word " * 50 + "end.9 Next sentence starts here and is marked."
    ul = [False] * len(text)
    m = text.index("marked")
    ul[m:m + 6] = [True] * 6
    a, _ = marked_edges(text, ul)
    assert text[a:].startswith("Next")
    a, _ = marked_edges(text, ul, starts={0})
    assert a == 0


def test_footnote_numbers_glued_to_words_are_not_read():
    from cardcutter.cutter.rules import unread_footnotes
    from cardcutter.tokenize import tokenize

    text = "this reduced U.S. stockpiles10 and F35 jets and COVID19 cases rose 9.3 percent to 2,000"
    toks = tokenize(text)
    hl = [True] * len(text)
    assert unread_footnotes(text, toks, hl) == 1
    unread = "".join(c for c, h in zip(text, hl) if not h)
    assert unread == "10"
