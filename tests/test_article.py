"""Ingest, citation, locate, retrieval, and whole-article cutting (no model calls)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from cardcutter.card import Card, FText
from cardcutter.cite.extract import resolve_meta
from cardcutter.cite.format import build_cite, split_name
from cardcutter.cite.meta import CiteMeta, find_doi, parse_date
from cardcutter.corpus.index import CardIndex, excluded_ids
from cardcutter.cutter.article import cut_document, meta_from_options
from cardcutter.cutter.locate import locate
from cardcutter.ingest.source import clean_paragraph, from_pdf, from_text
from cardcutter.verify.integrity import verify_body
from tests.fakes import FakeBackend, make_config, registry_with

ARTICLE = """Jane Q. Doe is a professor of marine policy at Example University.

Experts warn that disputes over marine protected areas (MPAs) will esca-
late. Without a treaty, marine protected areas collapse and biodiversity
loss becomes irreversible.

A second paragraph adds that enforcement is weak.   Patrol budgets fell by half."""


# -- ingest ---------------------------------------------------------------------------------
def test_from_text_cleans_layout_but_not_wording():
    doc = from_text(ARTICLE)
    assert len(doc.paragraphs) == 3
    assert "will escalate. Without a treaty" in doc.paragraphs[1]
    assert "weak. Patrol" in doc.paragraphs[2]
    assert doc.text == "\n".join(doc.paragraphs)
    a, b = doc.para_spans()[1]
    assert doc.text[a:b] == doc.paragraphs[1]


def test_clean_paragraph_keeps_real_hyphens():
    assert clean_paragraph("long-\nterm") == "long-term" or clean_paragraph("long-\nterm") == "longterm"
    assert clean_paragraph("Anglo-\nAmerican") == "Anglo-American"
    assert clean_paragraph("a  b\n c") == "a b c"


def test_from_pdf_drops_running_heads_and_page_numbers(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    pdf = pymupdf.open()
    pdf.set_metadata({"author": "Jane Q. Doe", "title": "Ocean Treaties", "creationDate": "D:20260107000000"})
    for n in range(4):
        page = pdf.new_page()
        page.insert_text((72, 40), "JOURNAL OF EXAMPLE STUDIES")
        body = ["Treaties fail without enforcement. doi 10.1234/abc.0", "Patrols matter more than paper.",
                "Fishing fleets adapt quickly.", "Body text on the last page ends here."][n]
        page.insert_text((72, 120), body)
        page.insert_text((300, 800), str(n + 1))
    path = tmp_path / "a.pdf"
    pdf.save(str(path))
    doc = from_pdf(path)
    joined = " ".join(doc.paragraphs)
    assert "JOURNAL OF EXAMPLE STUDIES" not in joined
    assert "Body text on the last page ends here." in joined and "Patrols matter" in joined
    assert not any(p.strip().isdigit() for p in doc.paragraphs)
    assert doc.meta.authors == ["Jane Q. Doe"] and doc.meta.date == "2026-01-07"
    assert doc.meta.doi == "10.1234/abc.0"


# -- cite -----------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("2026-01-07", (2026, 1, 7)),
    ("1/7/2026", (2026, 1, 7)),
    ("January 7, 2026", (2026, 1, 7)),
    ("Jan 2026", (2026, 1, None)),
    ("", (None, None, None)),
])
def test_parse_date(raw, expected):
    d = parse_date(raw)
    assert (d.year, d.month, d.day) == expected


def test_split_name_and_doi():
    assert split_name("Dr. Thomas E. Rotnem") == ("Thomas E.", "Rotnem")
    assert split_name("Levine, Evan") == ("Evan", "Levine")
    assert split_name("Smith Jr.") == ("", "Smith")
    assert find_doi("see https://doi.org/10.1080/13669877.2011.571786.") == "10.1080/13669877.2011.571786"


def test_inline_cite_marks_short_cite_and_reports_missing():
    meta = CiteMeta(authors=["Dr. Jane Q. Doe"], quals=["Professor of Marine Policy"], date="2026-01-07",
                    title="Ocean Treaties", publication="Example Review", url="https://example.org/a")
    res = build_cite(meta, "inline")
    assert res.cite.text == ('Dr. Jane Q. Doe 26, Professor of Marine Policy, "Ocean Treaties," Example Review, '
                             '01/07/2026, https://example.org/a')
    bold = "".join(ch for ch, on in zip(res.cite.text, res.cite.marks["cite13"]) if on)
    assert bold == "Doe 26" and res.missing == []

    bare = build_cite(CiteMeta(authors=["Jane Doe"], title="T"), "inline")
    assert "[quals?]" in bare.cite.text and "[date?]" in bare.cite.text
    assert bare.missing == ["date", "quals", "publication", "url"]


def test_bracketed_cite():
    meta = CiteMeta(authors=["Evan Levine"], quals=["B.A. in Astronomy"], date="2011-09-16",
                    title="Risk", publication="Journal of Risk Research", url="https://x.org")
    res = build_cite(meta, "bracketed", cutter="NM")
    assert res.cite.text == ("Levine ’11 [Evan; September 16; B.A. in Astronomy; Journal of Risk Research, "
                             "“Risk,” https://x.org, NM]")
    assert "".join(ch for ch, on in zip(res.cite.text, res.cite.marks["cite13"]) if on) == "Levine ’11"


def test_quals_not_in_the_article_are_dropped(tmp_path):
    cfg = make_config(tmp_path)

    def respond(stage, prompt):
        assert stage == "quals"
        return {"authors": ["Jane Q. Doe", "Invented Person"],
                "quals": ["professor of marine policy at Example University", "Nobel laureate"],
                "date": "", "publication": ""}

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    meta, notes = resolve_meta(from_text(ARTICLE), reg, use_crossref=False)
    assert meta.authors == ["Jane Q. Doe"]
    assert meta.quals == ["professor of marine policy at Example University"]
    assert any("dropped 2" in n for n in notes)


def test_manual_overrides_win():
    meta = CiteMeta(authors=["A B"], title="Old").override(**meta_from_options(authors="C D; E F", title="New", url=None))
    assert meta.authors == ["C D", "E F"] and meta.title == "New" and "manual" in meta.sources


# -- locate ---------------------------------------------------------------------------------
def test_short_document_is_one_candidate_without_a_call(tmp_path):
    cfg = make_config(tmp_path)
    fake = FakeBackend("fake", lambda s, p: pytest.fail("no call expected"))
    cands = locate(registry_with(cfg, fake=fake), cfg, from_text(ARTICLE), "claim")
    assert len(cands) == 1 and cands[0].start_para == 0 and cands[0].end_para == 2


def test_locate_trims_long_windows_and_skips_overlaps(tmp_path):
    cfg = make_config(tmp_path, max_window_words=60)
    paras = [f"Paragraph {i} " + " ".join(["word"] * 28) + "." for i in range(8)]  # 30 words each
    doc = from_text("\n\n".join(paras))

    def respond(stage, prompt):
        assert stage == "locate" and "[7] (30 words)" in prompt.user
        return {"candidates": [{"start": 2, "end": 6, "rationale": "best"},
                               {"start": 3, "end": 3, "rationale": "overlaps"},
                               {"start": 0, "end": 0, "rationale": "intro"},
                               {"start": 99, "end": 120, "rationale": "out of range"}]}

    cands = locate(registry_with(cfg, fake=FakeBackend("fake", respond)), cfg, doc, "claim", k=3)
    assert [(c.start_para, c.end_para) for c in cands] == [(2, 3), (0, 1)]   # (0, 0) widened to 60 words
    assert doc.text[cands[0].char_start:cands[0].char_end].startswith("Paragraph 2 ")
    assert cands[0].words == 60


# -- retrieval ------------------------------------------------------------------------------
def _card(cid: str, body: str, tag: str = "t") -> Card:
    ft = FText(body)
    ft.set_span("underline", 0, len(body))
    ft.set_span("highlight", 0, 5, "yellow")
    return Card(tag=FText(tag), cite=FText("c"), body=ft, meta={"id": cid})


def test_holdout_exclusion_covers_duplicate_bodies():
    cards = [_card("aff#1", "Same body text."), _card("1ac#9", "Same body  text."), _card("x#2", "Other.")]
    assert excluded_ids(cards, {"aff#1"}) == {"aff#1", "1ac#9"}


def test_index_dedupes_and_searches():
    cards = [_card("a", "Alpha text."), _card("b", "Alpha  text."), _card("c", "Gamma.")]
    embed = lambda texts: [[1.0, 0.0] if "Alpha" in t else [0.0, 1.0] for t in texts]  # noqa: E731
    idx = CardIndex.build("m", cards, embed)
    assert idx.ids == ["a", "c"]
    assert [i for i, _ in idx.search([1.0, 0.1], 2)] == ["a", "c"]
    assert [i for i, _ in idx.search([1.0, 0.1], 2, exclude={"a"})] == ["c"]
    assert np.allclose(np.linalg.norm(idx.vecs, axis=1), 1.0)


# -- whole article --------------------------------------------------------------------------
def test_cut_document_builds_a_verified_card_with_cite(tmp_path):
    cfg = make_config(tmp_path, acronym_rule=False)

    def respond(stage, prompt):
        if stage == "quals":
            return {"authors": ["Jane Q. Doe"], "quals": ["professor of marine policy at Example University"],
                    "date": "", "publication": ""}
        if stage == "underline":
            return {"plan": "p", "underline": [{"start": 12, "end": 40}]}
        if stage == "highlight":
            return {"plan": "p", "highlight": [{"start": 12, "end": 17}], "partial": []}
        if stage == "emphasis":
            return {"emphasis": []}
        if stage == "tag":
            return {"plan": "p", "tag": "MPA disputes escalate", "underline": ["escalate"]}
        if stage == "readback":
            return {"grammatical": 5, "coherent": 5, "proves_tag": 4, "power_tag": False, "feedback": "ok"}
        if stage == "intent":
            return {"verdict": "endorses", "author_conclusion": "", "reason": "the author argues it"}
        raise AssertionError(stage)

    doc = from_text(ARTICLE)
    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    [draft] = cut_document(reg, cfg, doc, "MPA disputes escalate",
                           cite_overrides=meta_from_options(date="2026-01-07"))
    card = draft.card
    assert card.tag.text == "MPA disputes escalate"
    assert card.cite.text.startswith("Jane Q. Doe 26, professor of marine policy at Example University")
    assert set(draft.cite_missing) == {"title", "publication", "url"}
    assert any("cite is missing" in w for w in draft.warnings)
    verify_body(card.body, doc.text, offset=card.meta["offset"])          # exact excerpt, again
    assert card.body.read_aloud().startswith("Experts warn that disputes over")
    json.dumps(card.to_json())                                             # serializable for the UI


def test_locate_shortlists_by_word_overlap_without_an_embedder(tmp_path):
    cfg = make_config(tmp_path, max_window_words=40)
    paras = [f"Filler paragraph number {i} about unrelated weather patterns and crops." for i in range(300)]
    paras[150] = "Marine protected areas collapse without enforcement treaties, experts say."
    doc = from_text("\n\n".join(paras))
    seen = {}

    def respond(stage, prompt):
        seen["prompt"] = prompt.user
        return {"candidates": [{"start": 150, "end": 150, "rationale": "on point"}]}

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    reg.backend("fake").num_ctx = 2400          # force the shortlist path
    [cand] = locate(reg, cfg, doc, "marine protected areas collapse without treaties")
    assert cand.start_para == 150 and "[150]" in seen["prompt"] and "[299]" not in seen["prompt"]


def test_window_edges_drop_page_chrome(tmp_path):
    cfg = make_config(tmp_path)
    doc = from_text("Share this article\n\n" + ARTICLE.split("\n\n", 1)[1] + "\n\nHarvard\n\nRIS\n\nVancouver")
    [cand] = locate(registry_with(cfg, fake=FakeBackend("fake", lambda s, p: {})), cfg, doc, "claim")
    window = doc.text[cand.char_start:cand.char_end]
    assert window.startswith("Experts warn") and window.endswith("Patrol budgets fell by half.")


def test_short_windows_are_widened_with_neighboring_prose(tmp_path):
    cfg = make_config(tmp_path, max_window_words=400, min_window_words=150)
    paras = [f"Paragraph {i} " + " ".join(["word"] * 58) + "." for i in range(20)]   # 60 words each
    doc = from_text("\n\n".join(paras))

    def respond(stage, prompt):
        return {"candidates": [{"start": 5, "end": 5, "rationale": "one paragraph"}]}

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    reg.backend("fake").num_ctx = 100000
    [c] = locate(reg, cfg, doc, "claim")
    assert (c.start_para, c.end_para) == (5, 7) and c.words == 180   # grows forward first


def test_windows_step_over_subheadings_and_stop_at_the_author_box(tmp_path):
    cfg = make_config(tmp_path, max_window_words=400, min_window_words=150)
    prose = lambda i: f"Paragraph {i} " + " ".join(["word"] * 58) + "."  # noqa: E731
    paras = [prose(0), prose(1), "The Danger Zones", prose(3), prose(4), "About the Author: Jane Doe",
             "Jane Doe is a writer whose work has appeared in many places and who lives in a city."]
    doc = from_text("\n\n".join(paras + [prose(i) for i in range(7, 12)]))

    def run(start, end):
        reg = registry_with(cfg, fake=FakeBackend("fake", lambda s, p: {"candidates": [
            {"start": start, "end": end, "rationale": "r"}]}))
        reg.backend("fake").num_ctx = 100000
        [c] = locate(reg, cfg, doc, "claim")
        return c.start_para, c.end_para

    assert run(1, 1) == (1, 4)   # stepped over "The Danger Zones" (the heading stays in, unread)
    assert run(3, 6) == (1, 4)   # stops before the author box, then widens backward


def test_cards_stop_before_a_bio_paragraph_and_site_boilerplate():
    from cardcutter.cite.meta import CiteMeta
    from cardcutter.cutter.locate import _before_bio
    from cardcutter.ingest.source import Document
    body = [f"Paragraph {i} " + " ".join(["word"] * 20) + "." for i in range(6)]
    tail = ["Gonçalo Manuel Soares Oliveira holds a degree in Euro-Atlantic Studies from the University of the Azores.",
            "The views and opinions expressed here are those of the author."]
    doc = Document(body + tail, CiteMeta(authors=["Gonçalo Oliveira"]))
    assert _before_bio(doc, 3, 7) == 5
    doc2 = Document(body + tail[1:], CiteMeta(authors=["Someone Else"]))
    assert _before_bio(doc2, 3, 6) == 5
    early = Document(body[:1] + ["Oliveira is right that drones changed the war, and here is why it matters a lot."] + body[1:],
                     CiteMeta(authors=["Gonçalo Oliveira"]))
    assert _before_bio(early, 0, 3) == 3     # a name early in the article is not a bio


def test_a_paragraph_ending_in_a_footnote_number_is_prose():
    from cardcutter.cutter.locate import is_prose
    assert is_prose("It was pre-crisis procurement and investments that led to this variation in output.8")
    assert is_prose("Shortages are not new for the U.S. military or the defense industrial base. [12]")
    assert not is_prose("Munitions Shortages and Mitigations Since World War II")


def test_an_organization_cited_as_author_gets_no_quals_marker():
    res = build_cite(CiteMeta(title="Taiwan: Defense and Military Issues", publication="Congressional Research Service",
                              date="2026-06-18", url="https://example.org/r"), "inline")
    assert res.cite.text.startswith("Congressional Research Service 26, \"Taiwan") and "quals" not in res.missing


def test_cite_drops_zero_width_characters():
    from cardcutter.cite.format import build_cite
    from cardcutter.cite.meta import CiteMeta

    res = build_cite(CiteMeta(authors=["Alex\u200bander Velez-Green"], date="2023-08-31", title="Trade\u00ad-offs",
                              publication="Heritage"))
    text = res.cite.text
    assert "Alexander Velez-Green" in text and "\u200b" not in text and "\u00ad" not in text
