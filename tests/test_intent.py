"""The author's-view check: the article frame it reads, quote verification, warnings, and how
cut_document and auto mode use its verdict."""
from __future__ import annotations

from cardcutter.card import FText
from cardcutter.cutter import article as article_mod
from cardcutter.cutter.intent import (CARD_END, CARD_START, article_frame, check_author_view, quote_in_text,
                                      warning_for)
from cardcutter.cutter.locate import Candidate
from cardcutter.cutter.pipeline import CutResult
from cardcutter.ingest.source import Document, from_text
from cardcutter.search import auto as auto_mod
from cardcutter.search.providers import SearchHit
from cardcutter.search.rank import Source
from tests.fakes import FakeBackend, make_config, registry_with

ARTICLE = [
    "Less than a year ago, a company announced it had resurrected the dire wolf.",
    "Critics have argued de-extinction potentially undermines support for conserving existing species.",
    "In other words, de-extinction technology poses a moral hazard.",
    "We presented 363 people with several scenarios.",
    "We found no evidence that proposing de-extinction makes people more accepting of extinction.",
    "Our results show claims that de-extinction will necessarily create a moral hazard are unjustified.",
]


def test_short_article_is_shown_whole_with_the_card_marked():
    frame = article_frame(Document(ARTICLE), 1, 2)
    lines = frame.split("\n\n")
    assert lines[0] == ARTICLE[0] and lines[1] == CARD_START and lines[4] == CARD_END
    assert ARTICLE[5] in frame and "…" not in frame


def test_long_article_keeps_opening_card_conclusions_and_end_but_not_references():
    filler = "The study area has many reefs and the survey covered them over several seasons " * 4
    paras = ["Opening paragraph about de-extinction."] + [f"{k}. {filler}" for k in range(40)]
    paras[20] = "The card passage: critics say de-extinction creates a moral hazard."
    paras[33] = "Overall, we found no evidence of a moral hazard in any scenario."
    paras += ["In closing, communication matters.", "References", "Smith J (2020) A paper. Journal 1:1."]
    frame = article_frame(Document(paras), 20, 20, max_words=600)
    assert "Opening paragraph" in frame and CARD_START + "\n\n" + paras[20] in frame
    assert paras[33] in frame                  # conclusion-type sentence found far from the card
    assert "In closing" in frame and "Smith J (2020)" not in frame
    assert "…" in frame and len(frame.split()) < 900


def test_quote_must_appear_in_the_article():
    text = "Our results show claims that de‑extinction will necessarily create a “moral hazard” are unjustified."
    assert quote_in_text('Our results show claims that de‑extinction will necessarily create a "moral hazard"', text)
    assert not quote_in_text("Our results prove de-extinction is always safe", text)
    assert not quote_in_text("are unjustified", text)  # too short to mean anything


def test_check_drops_an_invented_quote_and_warns_on_disputes():
    cfg = make_config_with_intent()
    answers = iter([
        {"verdict": "disputes", "author_conclusion": ARTICLE[5], "reason": "the study rejects it"},
        {"verdict": "disputes", "author_conclusion": "De-extinction is always morally fine, the authors say.", "reason": "r"},
    ])
    reg = registry_with(cfg, fake=FakeBackend("fake", lambda s, p: next(answers)))
    view, _ = check_author_view(reg, Document(ARTICLE), 1, 2, "Creates moral hazard", "undermines support")
    assert view["verdict"] == "disputes" and view["author_conclusion"] == ARTICLE[5]
    w = warning_for(view)
    assert "argues against" in w and "unjustified" in w
    view, _ = check_author_view(reg, Document(ARTICLE), 1, 2, "Creates moral hazard", "undermines support")
    assert view["author_conclusion"] == "" and not view["quote_found"]
    assert warning_for({"verdict": "endorses"}) is None
    assert "limits this claim" in warning_for({"verdict": "qualifies", "author_conclusion": "", "reason": ""})


def make_config_with_intent(**cut):
    import tempfile
    from pathlib import Path

    return make_config(Path(tempfile.mkdtemp()), **cut)


def test_cut_document_records_the_view_and_warns(monkeypatch):
    cfg = make_config_with_intent()
    doc = Document(ARTICLE, origin="https://example.org/a")
    cand = Candidate(1, 2, len(ARTICLE[0]) + 1, len("\n".join(ARTICLE[:3])), 20, "r")
    monkeypatch.setattr(article_mod, "locate", lambda *a, **k: [cand])
    monkeypatch.setattr(article_mod, "cut_window", lambda *a, **k: CutResult(
        body=FText("\n".join(ARTICLE[1:3])), offset=cand.char_start, tag=FText("Creates moral hazard")))
    calls = []

    def respond(stage, prompt):
        calls.append(stage)
        if stage == "intent":
            assert CARD_START in prompt.user and "Creates moral hazard" in prompt.user
            return {"verdict": "disputes", "author_conclusion": ARTICLE[5], "reason": "rejects it"}
        return {"authors": [], "quals": [], "date": "", "publication": ""}

    reg = registry_with(cfg, fake=FakeBackend("fake", respond))
    [d] = article_mod.cut_document(reg, cfg, doc, "claim")
    assert "intent" in calls and d.author_view["verdict"] == "disputes"
    assert any("argues against" in w for w in d.warnings)
    assert d.card.meta["author_view"]["verdict"] == "disputes"
    assert any(log.stage == "intent" for log in d.result.logs)

    calls.clear()
    cfg.data["cut"]["author_check"] = False
    [d] = article_mod.cut_document(reg, cfg, doc, "claim")
    assert "intent" not in calls and d.author_view is None


def _source(origin):
    doc = from_text("Experts warn that disputes over marine protected areas will escalate. " * 30, origin=origin)
    return Source(origin[-6:], doc, SearchHit(origin), 0.5, 0.5, (0, 0))


def test_auto_mode_never_uses_a_card_the_author_disputes(monkeypatch):
    cfg = make_config_with_intent()

    def fake_cut(reg, cfg, doc, claim, **kw):
        verdict = "disputes" if "against" in doc.origin else "qualifies" if "hedged" in doc.origin else "endorses"

        class R:
            readback = {"proves_tag": 4, "min": 4}
        return [type("D", (), {"result": R(), "author_view": {"verdict": verdict, "author_conclusion": "We found none."}})()]

    monkeypatch.setattr(auto_mod, "cut_document", fake_cut)
    sources = [_source("https://against.com/1"), _source("https://hedged.com/1"), _source("https://fine.com/1")]
    done, skipped = auto_mod.auto_cut(registry_with(cfg), cfg, "claim", sources, n_cards=3)
    assert [s.doc.origin for s, _ in done] == ["https://hedged.com/1", "https://fine.com/1"]  # qualified: kept, warned
    assert len(skipped) == 1 and "against.com" in skipped[0] and "argues against" in skipped[0]
